import sys
import asyncio

# Set the event loop policy on Windows to support subprocesses
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import subprocess

# Command to install Playwright browser binaries
command = ["playwright", "install"]

try:
    # Execute the command
    result = subprocess.run(command, check=True)
    result = subprocess.run(["sudo", "playwright", "install-deps"], check=True)
    print("Playwright browser binaries installed successfully.")
except subprocess.CalledProcessError as e:
    print(f"Failed to install Playwright browser binaries: {e}")

import streamlit as st
import time
import os
from urllib.parse import quote
import google.generativeai as genai
from newspaper import Article, ArticleException
from concurrent.futures import ThreadPoolExecutor, as_completed
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode

# Set page configuration
st.set_page_config(
    page_title="Global News Summarizer",
    page_icon="📰",
    layout="wide",
)

# Apply custom CSS
st.markdown("""
<style>
    .main {
        padding: 2rem;
    }
    .stProgress > div > div > div {
        background-color: #4CAF50;
    }
    .news-source {
        font-weight: bold;
        color: #1E88E5;
    }
    .summary-header {
        font-size: 1.5rem;
        font-weight: bold;
        margin-bottom: 1rem;
        color: #212121;
    }
    .processing-header {
        font-size: 1.2rem;
        font-weight: bold;
        margin-top: 1rem;
        color: #424242;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Gemini API
@st.cache_resource
def initialize_genai():
    api_key = "AIzaSyDrksJ5fvEXs7ZCKS4qQ4-LFgP8M4MZAE0"
    
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-2.0-flash')

# Function to extract article links from Crawl4AI search results
def extract_article_links(result, source):
    if not result.success:
        return []
    
    internal_links = result.links.get("internal", [])
    external_links = result.links.get("external", [])
    
    all_links = internal_links + external_links
    filtered_links = []
    
    # Filter links based on the source
    if source == "BBC":
        filtered_links = [link["href"] for link in all_links 
                         if "bbc.com" in link["href"] and 
                         ("/news/" in link["href"] or "/sport/" in link["href"])]
    elif source == "CNN":
        filtered_links = [link["href"] for link in all_links 
                         if "cnn.com" in link["href"] and 
                         not "/search?" in link["href"]]
    elif source == "NBC News":
        filtered_links = [link["href"] for link in all_links 
                         if "nbcnews.com" in link["href"] and 
                         not "/search?" in link["href"]]
    elif source == "Google News":
        filtered_links = [link["href"] for link in all_links 
                         if "news.google.com/articles" in link["href"]]
    elif source == "New York Times":
        filtered_links = [link["href"] for link in all_links 
                         if "nytimes.com" in link["href"] and 
                         not "/search?" in link["href"]]
    
    # Remove duplicates while preserving order
    unique_links = []
    for link in filtered_links:
        if link not in unique_links and "javascript:" not in link.lower():
            unique_links.append(link)
    
    return unique_links[:5]  # Return top 5 links

# Function to extract article content using newspaper3k
def extract_article_content(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        
        # Return a dictionary with the article's content
        return {
            "title": article.title,
            "text": article.text,
            "authors": article.authors,
            "publish_date": article.publish_date,
            "success": True
        }
    except ArticleException as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Function to search news and extract content
async def search_and_extract(topic, progress_bar, status_text, sources_status, extraction_status):
    search_urls = {
        "BBC": f"https://www.bbc.com/search?q={quote(topic)}",
        "CNN": f"https://edition.cnn.com/search?q={quote(topic)}",
        "NBC News": f"https://www.nbcnews.com/search/?q={quote(topic)}",
        "Google News": f"https://news.google.com/search?q={quote(topic)}",
        "New York Times": f"https://www.nytimes.com/search?query={quote(topic)}"
    }
    
    news_data = {}
    
    # Configure Crawl4AI
    browser_config = BrowserConfig(verbose=False)
    search_config = CrawlerRunConfig(
        word_count_threshold=5,
        process_iframes=True,
        remove_overlay_elements=True,
        exclude_social_media_links=True,
        wait_for_images=False,
        cache_mode=CacheMode.ENABLED
    )
    
    # First pass: get article links from search results using Crawl4AI
    status_text.text("Searching for news articles...")
    progress_bar.progress(10)
    
    sources_status_md = ""
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for source, url in search_urls.items():
            try:
                # Crawl the search page
                search_result = await crawler.arun(url=url, config=search_config)
                
                if search_result.success:
                    # Extract article links from the crawled page
                    article_links = extract_article_links(search_result, source)
                    news_data[source] = {"search_url": url, "article_links": article_links}
                    sources_status_md += f"- **{source}**: Found {len(article_links)} articles\n"
                else:
                    news_data[source] = {"search_url": url, "article_links": []}
                    sources_status_md += f"- **{source}**: Search failed - {search_result.error_message}\n"
            except Exception as e:
                news_data[source] = {"search_url": url, "article_links": []}
                sources_status_md += f"- **{source}**: Error during search - {str(e)}\n"
    
    sources_status.markdown(sources_status_md)
    progress_bar.progress(40)
    
    # Second pass: extract content from article links using newspaper3k with ThreadPoolExecutor
    status_text.text("Extracting article content...")
    extraction_status_md = ""
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Create a mapping of futures to their source and URL
        future_to_source = {}
        
        # Submit all article extraction tasks
        for source, data in news_data.items():
            article_contents = []
            news_data[source]["articles"] = article_contents
            
            for link in data["article_links"]:
                future = executor.submit(extract_article_content, link)
                future_to_source[future] = (source, link)
        
        # Process completed tasks as they finish
        source_completed = {source: 0 for source in news_data.keys()}
        source_successful = {source: 0 for source in news_data.keys()}
        total_tasks = sum(len(data["article_links"]) for data in news_data.values())
        completed_tasks = 0
        
        for future in as_completed(future_to_source):
            source, link = future_to_source[future]
            
            try:
                result = future.result()
                source_completed[source] += 1
                completed_tasks += 1
                
                # Update progress bar
                progress_percent = 40 + (completed_tasks * 40 / total_tasks)
                progress_bar.progress(int(progress_percent))
                
                if result["success"]:
                    source_successful[source] += 1
                    news_data[source]["articles"].append({
                        "url": link,
                        "content": result["text"]
                    })
            except Exception as e:
                source_completed[source] += 1
                completed_tasks += 1
                
                # Update progress even on error
                progress_percent = 40 + (completed_tasks * 40 / total_tasks)
                progress_bar.progress(int(progress_percent))
    
    # Update extraction status
    for source in news_data.keys():
        articles_with_content = source_successful[source]
        
        if articles_with_content > 0:
            extraction_status_md += f"- **{source}**: Successfully extracted content from {articles_with_content} articles\n"
        else:
            extraction_status_md += f"- **{source}**: Unable to extract content (possible scraping restrictions)\n"
    
    extraction_status.markdown(extraction_status_md)
    
    return news_data

# Generate summary with Gemini
def generate_summary(text, topic):
    model = initialize_genai()
    
    prompt = f"""
    Below is a collection of news article texts about "{topic}" from multiple sources.
    
    {text}
    
    Provide a concise summary of approximately 200 words about what is currently happening 
    regarding "{topic}" based on these articles. Format the summary as short, direct sentences 
    that capture the key points. Focus on the most recent developments and important context.
    
    If the content doesn't contain enough relevant information, mention that the available 
    news sources had limited information about this topic.
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Unable to generate summary due to an error: {str(e)}"

# Main app function
def main():
    st.title("📰 Global News Summarizer")
    
    st.markdown("""
    This app searches for the latest news from multiple sources (BBC, CNN, NBC News, 
    Google News, and New York Times) on a topic you provide, extracts the content, 
    and generates a concise summary using Google's Gemini AI.
    """)
    
    # User input
    topic = st.text_input("Enter a news topic to search:", "")
    
    if st.button("Generate Summary") and topic:
        # Create placeholder for progress bar and status
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Create containers for displaying information
        processing_container = st.container()
        summary_container = st.container()
        
        with processing_container:
            st.markdown('<div class="processing-header">Processing News Data</div>', unsafe_allow_html=True)
            sources_status = st.empty()
            extraction_status = st.empty()
        
        try:
            # Search and extract news content
            news_data = asyncio.run(search_and_extract(topic, progress_bar, status_text, sources_status, extraction_status))
            
            # Process extracted data
            progress_bar.progress(80)
            status_text.text("Processing extracted content...")
            
            all_content = ""
            total_articles_with_content = 0
            
            for source, data in news_data.items():
                articles_with_content = len(data.get("articles", []))
                total_articles_with_content += articles_with_content
                
                # Combine all article content
                for article in data.get("articles", []):
                    all_content += f"\n{article['content']}\n"
            
            # Generate summary if we have content
            if all_content:
                progress_bar.progress(90)
                status_text.text("Generating summary with Gemini AI...")
                
                # Limit content size if too large
                if len(all_content) > 30000:
                    all_content = all_content[:30000] + "..."
                
                summary = generate_summary(all_content, topic)
                
                progress_bar.progress(100)
                status_text.text("Summary generation complete!")
                
                # Display the summary
                with summary_container:
                    st.markdown(f'<div class="summary-header">Summary of "{topic}" News</div>', unsafe_allow_html=True)
                    st.markdown(summary)
                    
                    # Display statistics
                    st.markdown("---")
                    st.markdown(f"**Sources analyzed**: {len(news_data)}")
                    st.markdown(f"**Articles processed**: {total_articles_with_content}")
                    st.markdown(f"**Content length**: {len(all_content)} characters")
                    
                    # Add timestamp
                    st.markdown(f"**Generated on**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                progress_bar.progress(100)
                status_text.text("Processing complete, but no content could be extracted.")
                
                with summary_container:
                    st.error("Unable to extract content from any news sources. This could be due to scraping restrictions or no relevant articles found.")
        
        except Exception as e:
            progress_bar.progress(100)
            status_text.text("An error occurred")
            st.error(f"Error: {str(e)}")

if __name__ == "__main__":
    main()

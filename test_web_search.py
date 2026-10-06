from trafilatura import fetch_url, extract

# 1. Choose a URL to scrape
url = 'https://zapier.com/blog/ai-prompt-templates/'

# 2. Download the HTML content
downloaded = fetch_url(url)

# 3. Extract the main text (it returns None if extraction fails)
result = extract(downloaded, output_format= "json")

if result:
    print(f"Successfully extracted {len(result)} characters.")
    print("-" * 30)
    print(result)
else:
    print("Could not extract content from this URL.")

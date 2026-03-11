from main import search_web

def main():
    # Test the search_web function
    results = search_web("latest news on AI", max_results=5, geo_focus="global", time_horizon="last_30_days")
    print("Search Results:" + "\n"+ results)
    for idx, result in enumerate(results):
        print(f"Result {idx + 1}:")
        print(f"Title: {result['title']}")
        print(f"Link: {result['link']}")
        print(f"Snippet: {result['snippet']}\n")
    
if main.__name__ == "__main__":
    # Test the search_web function
    main()

        

#!/usr/bin/env python3
"""
AI Company Finder
Scrapes and aggregates AI companies from multiple sources
"""

import requests
from bs4 import BeautifulSoup
import json
import csv
import time
from datetime import datetime
from typing import List, Dict
import argparse

class AICompanyFinder:
    def __init__(self):
        self.companies = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_yc_companies(self, batch: str = None, tags: List[str] = None):
        """
        Search Y Combinator companies
        Batches: W26, S25, W25, S24, W24, S23, etc.
        Tags: AI, ML, Voice AI, Developer Tools, etc.
        """
        print(f"🔍 Searching YC companies...")
        
        # YC API endpoint (if available) or work at a startup
        base_url = "https://www.ycombinator.com/companies"
        
        # Build query parameters
        params = []
        if batch:
            params.append(f"batch={batch}")
        if tags:
            for tag in tags:
                params.append(f"tags={tag.replace(' ', '+')}")
        
        query = "&".join(params) if params else ""
        url = f"{base_url}?{query}" if query else base_url
        
        print(f"   URL: {url}")
        
        # Note: This would need actual scraping logic
        # For now, returning structure
        return {
            "source": "Y Combinator",
            "url": url,
            "method": "manual_browse",
            "note": "Visit this URL and filter by tags like 'AI', 'Voice AI', 'ML Infrastructure'"
        }
    
    def search_work_at_startup(self, keywords: List[str]):
        """
        Search YC's Work at a Startup platform
        """
        print(f"🔍 Searching Work at a Startup...")
        
        base_url = "https://www.workatastartup.com/companies"
        
        results = []
        for keyword in keywords:
            url = f"{base_url}?query={keyword.replace(' ', '+')}"
            results.append({
                "keyword": keyword,
                "url": url,
                "source": "Work at a Startup"
            })
        
        return results
    
    def search_wellfound(self, keywords: List[str], locations: List[str] = None):
        """
        Search Wellfound (formerly AngelList Talent)
        """
        print(f"🔍 Searching Wellfound...")
        
        base_url = "https://wellfound.com/role"
        
        results = []
        for keyword in keywords:
            url = f"{base_url}/r/software-engineer/{keyword.replace(' ', '-')}"
            if locations:
                url += f"?locations={','.join(locations)}"
            
            results.append({
                "keyword": keyword,
                "url": url,
                "source": "Wellfound"
            })
        
        return results
    
    def search_crunchbase(self, keywords: List[str]):
        """
        Search Crunchbase for AI companies
        """
        print(f"🔍 Searching Crunchbase...")
        
        results = []
        for keyword in keywords:
            url = f"https://www.crunchbase.com/search/organizations?query={keyword.replace(' ', '+')}"
            results.append({
                "keyword": keyword,
                "url": url,
                "source": "Crunchbase",
                "note": "Filter by: Founded Date, Funding Stage, Location"
            })
        
        return results
    
    def search_ai_specific_sites(self):
        """
        AI-specific job boards and company directories
        """
        print(f"🔍 Searching AI-specific sites...")
        
        sites = [
            {
                "name": "AI Jobs",
                "url": "https://ai-jobs.net/",
                "focus": "AI/ML specific roles"
            },
            {
                "name": "Machine Learning Jobs",
                "url": "https://www.mljobs.com/",
                "focus": "ML engineering roles"
            },
            {
                "name": "AI Startups",
                "url": "https://aistartups.com/",
                "focus": "Directory of AI startups"
            },
            {
                "name": "There's An AI For That",
                "url": "https://theresanaiforthat.com/",
                "focus": "AI product directory (includes companies)"
            },
            {
                "name": "Futurepedia",
                "url": "https://www.futurepedia.io/",
                "focus": "AI tools and companies"
            }
        ]
        
        return sites
    
    def get_yc_batch_companies(self):
        """
        Get recent YC batch companies to manually review
        """
        batches = {
            "W26": "https://www.ycombinator.com/companies?batch=W26",
            "S25": "https://www.ycombinator.com/companies?batch=S25",
            "W25": "https://www.ycombinator.com/companies?batch=W25",
            "S24": "https://www.ycombinator.com/companies?batch=S24",
            "W24": "https://www.ycombinator.com/companies?batch=W24",
            "S23": "https://www.ycombinator.com/companies?batch=S23",
        }
        
        print("\n📊 Recent YC Batches:")
        for batch, url in batches.items():
            print(f"   {batch}: {url}")
        
        return batches
    
    def generate_search_queries(self, focus_areas: List[str]):
        """
        Generate Google search queries for finding companies
        """
        print(f"\n🔎 Google Search Queries:")
        
        templates = [
            '"{focus}" startup "series A" OR "seed round" 2024 OR 2025',
            '"{focus}" "YC" OR "Y Combinator" company',
            '"{focus}" startup "hiring" OR "we\'re hiring"',
            '"{focus}" "founded 2024" OR "founded 2025"',
            'site:techcrunch.com "{focus}" startup funding',
            'site:crunchbase.com "{focus}" companies',
        ]
        
        queries = []
        for focus in focus_areas:
            for template in templates:
                query = template.format(focus=focus)
                queries.append({
                    "focus": focus,
                    "query": query,
                    "url": f"https://www.google.com/search?q={query.replace(' ', '+')}"
                })
        
        for q in queries[:10]:  # Show first 10
            print(f"   • {q['query']}")
        
        return queries
    
    def save_results(self, results: Dict, filename: str = None):
        """
        Save results to JSON file
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ai_companies_{timestamp}.json"
        
        filepath = f"/mnt/user-data/outputs/{filename}"
        
        with open(filepath, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\n✅ Results saved to: {filepath}")
        return filepath
    
    def create_manual_research_guide(self):
        """
        Create a guide for manual research
        """
        guide = {
            "title": "AI Company Research Guide",
            "generated": datetime.now().isoformat(),
            
            "yc_companies": {
                "description": "Y Combinator companies by category",
                "urls": {
                    "All YC AI companies": "https://www.ycombinator.com/companies?tags=AI",
                    "Voice AI": "https://www.ycombinator.com/companies?tags=Voice+AI",
                    "Developer Tools": "https://www.ycombinator.com/companies?tags=Developer+Tools",
                    "ML Infrastructure": "https://www.ycombinator.com/companies?tags=ML+Infrastructure",
                    "Work at a Startup": "https://www.workatastartup.com/",
                },
                "filters": [
                    "Recent batches: W26, S25, W25, S24, W24",
                    "Team size: 2-50 employees",
                    "Actively hiring",
                    "Series A or earlier"
                ]
            },
            
            "job_boards": [
                {"name": "Wellfound", "url": "https://wellfound.com/", "focus": "Startup jobs"},
                {"name": "AI Jobs", "url": "https://ai-jobs.net/", "focus": "AI/ML roles"},
                {"name": "ML Jobs", "url": "https://www.mljobs.com/", "focus": "ML engineering"},
            ],
            
            "funding_databases": [
                {"name": "Crunchbase", "url": "https://www.crunchbase.com/", "note": "Filter by funding stage, founded date"},
                {"name": "PitchBook", "url": "https://pitchbook.com/", "note": "Requires subscription"},
                {"name": "Tracxn", "url": "https://tracxn.com/", "note": "Company intelligence"},
            ],
            
            "search_strategies": [
                "Search Twitter for: 'YC company hiring voice AI'",
                "Follow YC partners on Twitter for announcements",
                "Join YC Slack communities (if available)",
                "Check HN 'Who's Hiring' threads",
                "LinkedIn: Search 'YC W26' or 'YC S25' in people's profiles",
                "Product Hunt: New AI products often link to companies"
            ],
            
            "specific_focus_areas": {
                "Voice AI": [
                    "Bland AI", "Retell AI", "Vapi", "Bolna",
                    "ElevenLabs", "PlayHT", "Deepgram"
                ],
                "AI Infrastructure": [
                    "Modal Labs", "Replicate", "Baseten", "Cerebrium",
                    "Anyscale", "Together AI"
                ],
                "Developer Tools": [
                    "LangChain", "E2B", "Stainless", "WorkOS",
                    "Merge", "Inngest"
                ],
                "AI Agents": [
                    "Fixie.ai", "Arcade AI", "Adept",
                    "Multi-On", "Dust"
                ]
            },
            
            "networking_tips": [
                "Check your LinkedIn 2nd connections at YC companies",
                "Engage with YC founders on Twitter",
                "Comment on YC Launch HN posts",
                "Attend YC Demo Days (if possible)",
                "Join AI/ML communities (Discord, Slack)"
            ]
        }
        
        return guide


def main():
    parser = argparse.ArgumentParser(description='Find AI companies for job search')
    parser.add_argument('--focus', nargs='+', default=['Voice AI', 'AI Infrastructure', 'Developer Tools'],
                       help='Focus areas for search')
    parser.add_argument('--output', default='ai_companies_research.json',
                       help='Output filename')
    
    args = parser.parse_args()
    
    print("🤖 AI Company Finder")
    print("=" * 50)
    
    finder = AICompanyFinder()
    
    # Collect all results
    results = {
        "generated_at": datetime.now().isoformat(),
        "focus_areas": args.focus,
        "sources": {}
    }
    
    # YC Companies
    print("\n📍 Y COMBINATOR COMPANIES")
    print("-" * 50)
    results["sources"]["yc"] = finder.search_yc_companies(tags=args.focus)
    results["sources"]["yc_batches"] = finder.get_yc_batch_companies()
    
    # Work at a Startup
    print("\n📍 WORK AT A STARTUP")
    print("-" * 50)
    results["sources"]["work_at_startup"] = finder.search_work_at_startup(args.focus)
    
    # Wellfound
    print("\n📍 WELLFOUND")
    print("-" * 50)
    results["sources"]["wellfound"] = finder.search_wellfound(args.focus)
    
    # Crunchbase
    print("\n📍 CRUNCHBASE")
    print("-" * 50)
    results["sources"]["crunchbase"] = finder.search_crunchbase(args.focus)
    
    # AI-specific sites
    print("\n📍 AI-SPECIFIC SITES")
    print("-" * 50)
    results["sources"]["ai_sites"] = finder.search_ai_specific_sites()
    for site in results["sources"]["ai_sites"]:
        print(f"   • {site['name']}: {site['url']}")
    
    # Google search queries
    print("\n📍 GOOGLE SEARCH QUERIES")
    print("-" * 50)
    results["sources"]["google_queries"] = finder.generate_search_queries(args.focus)
    
    # Manual research guide
    print("\n📍 MANUAL RESEARCH GUIDE")
    print("-" * 50)
    results["manual_research_guide"] = finder.create_manual_research_guide()
    
    # Save results
    filepath = finder.save_results(results, args.output)
    
    print("\n" + "=" * 50)
    print("✅ NEXT STEPS:")
    print("   1. Review the JSON file for all URLs")
    print("   2. Visit YC company directories with filters")
    print("   3. Use Google search queries to find recent companies")
    print("   4. Check LinkedIn 2nd connections at target companies")
    print("   5. Set up Google Alerts for 'YC voice AI hiring'")
    print("=" * 50)
    
    return filepath


if __name__ == "__main__":
    main()
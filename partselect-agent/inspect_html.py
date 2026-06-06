import sys; sys.stdout.reconfigure(encoding='utf-8')
import json, re
from curl_cffi import requests as r
from bs4 import BeautifulSoup

s = r.Session(impersonate='chrome')

# Find symptom links on repair pages
for appliance in ['Refrigerator', 'Dishwasher']:
    print(f'\n=== {appliance} Repair Page ===')
    resp = s.get(f'https://www.partselect.com/Repair/{appliance}/', timeout=15)
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Find symptom links
    all_links = soup.find_all('a', href=True)
    symptom_links = [a for a in all_links if '/Repair/' in a.get('href','') and a.get('href','').count('/') >= 2]
    print(f'Symptom links found: {len(symptom_links)}')
    for a in symptom_links[:10]:
        href = a.get('href','')
        txt = a.get_text(strip=True)[:60].encode('ascii','replace').decode()
        print(f'  {href} -> {txt}')
    
    # Also look for symptom-specific elements
    symptom_els = soup.select('[class*="symptom"], [class*="repair-"]')
    print(f'Symptom-class elements: {len(symptom_els)}')

# Check one actual symptom page
print('\n=== Single Symptom Page ===')
resp2 = s.get('https://www.partselect.com/Repair/Refrigerator/Noisy/', timeout=15)
soup2 = BeautifulSoup(resp2.text, 'html.parser')
print(f'Status: {resp2.status_code}')
h1 = soup2.find('h1')
print(f'h1: {h1.get_text(strip=True)[:80] if h1 else "N/A"}')

# Find difficulty  
for sel in ['[class*="difficulty"]', '[class*="level"]', 'h2', 'h3', 'strong']:
    el = soup2.select_one(sel)
    if el:
        txt = el.get_text(strip=True).encode('ascii','replace').decode()
        if txt and len(txt) < 50:
            print(f'difficulty ({sel}): {txt}')
            break

# Find intro text
ps = soup2.select('p')
for p in ps[:3]:
    txt = p.get_text(strip=True)[:150].encode('ascii','replace').decode()
    if txt: print(f'para: {txt}')

# Parts
part_links = soup2.select('a[href*="/PS"]')
print(f'PS links: {len(part_links)}')
for a in part_links[:5]:
    href = a.get('href','')
    ps_match = re.search(r'PS\d+', href)
    txt = a.get_text(strip=True)[:50].encode('ascii','replace').decode()
    if ps_match: print(f'  {ps_match.group()}: {txt}')

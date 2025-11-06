
import os, re, hashlib, datetime, argparse, random, yaml, feedparser, trafilatura
from pathlib import Path
from openai import OpenAI

LLM_MODEL = os.getenv("LLM_MODEL","gpt-5")
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

ARTICLE_SYS = """You are an expert analyst-blogger. Write in Korean with depth and originality.
Rules:
- 900~1400 words.
- Start with a crisp thesis (1–2 sentences).
- Include 3–5 numbered 'Original Insights' with supporting evidence.
- Include 1 MECE-style framework table (markdown) if relevant.
- Provide a short 'Counterargument & Rebuttal' section.
- End with 'So what for operators' (3–5 bullets).
- Add a Sources list with exact URLs actually used.
- Avoid copying phrasing from sources > 20%. Paraphrase aggressively.
"""

def ask_llm(prompt, sys=None, temperature=0.7):
    msgs=[{"role":"system","content": sys or ARTICLE_SYS},
          {"role":"user","content": prompt}]
    r = client.chat.completions.create(model=LLM_MODEL, messages=msgs, temperature=temperature)
    return r.choices[0].message.content

def slugify(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^a-zA-Z0-9\- ]","", s).strip().lower().replace(" ","-")
    return re.sub(r"-+","-", s) or "post"

def md_frontmatter(meta:dict)->str:
    lines = ["---"]
    for k,v in meta.items():
        if isinstance(v, list):
            arr = ", ".join([f'"{str(x)}"' for x in v])
            lines.append(f"{k}: [{arr}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        else:
            val = str(v).replace('"','\\"')
            lines.append(f'{k}: "{val}"')
    lines.append("---\n")
    return "\n".join(lines)

def fetch_candidates(feeds, max_each=5):
    items=[]
    for f in feeds:
        d=feedparser.parse(f)
        for e in d.entries[:max_each]:
            url=e.get("link")
            if not url: continue
            html=trafilatura.fetch_url(url)
            text=trafilatura.extract(html, include_comments=False) if html else ""
            if text and len(text.split())>200:
                items.append({"title":e.get("title",""),"url":url,"text":text})
    return items

def dedup(items):
    seen=set(); out=[]
    for it in items:
        h=hashlib.md5(it["text"][:2000].encode()).hexdigest()
        if h in seen: continue
        seen.add(h); out.append(it)
    random.shuffle(out)
    return out

def make_article(sector, picked):
    urls = "\n".join([f"- {it['url']}" for it in picked])
    context = "\n\n".join([f"### Source {i+1}\n{it['text'][:1800]}" for i,it in enumerate(picked)])
    prompt = f"""
다음 자료를 바탕으로 '{sector['name']}' 섹터에 대한 독창적 분석 글을 작성해줘.

섹터 태그: {', '.join(sector.get('tags', []))}
핵심 키워드(가능하면 포함): {', '.join(sector.get('keywords', []))}

내가 참고한 링크:
{urls}

자료(일부발췌):
{context}
"""
    return ask_llm(prompt, ARTICLE_SYS)

def ensure_dir(p:Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def save_post(sector, title, body):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    slug = slugify(title)[:60]
    meta = {
        "title": title.strip(),
        "date": f"{today}T08:00:00-08:00",
        "tags": sector.get("tags", []),
        "categories": [sector["name"]],
        "sources": [s.get("url") for s in sector.get("_picked", []) if s.get("url")]
    }
    # Hugo content path per section
    section = sector["name"].replace(" ","-").lower()
    path = Path("content")/section/f"{today}-{slug}.md"
    ensure_dir(path)
    path.write_text(md_frontmatter(meta)+body, encoding="utf-8")
    return path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=int(os.getenv("POSTS", "1")),
                        help="이번 실행에서 생성할 글 개수")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path("content_sources.yaml").read_text(encoding="utf-8"))
    sectors = cfg["sectors"]
    total = max(1, args.count)

    # Round-robin across sectors
    written = []
    si = 0
    while len(written) < total:
        sector = sectors[si % len(sectors)]
        si += 1
        items = dedup(fetch_candidates(sector.get("feeds", []), max_each=8))
        if not items: 
            continue
        picked = items[:3]
        sector["_picked"] = picked
        # title suggestion
        try:
            title = ask_llm(f"아래 텍스트를 보고 한국어 블로그 제목 후보 5개를 제안하고, 그 중 최고 하나를 **한 줄만** 출력해줘:\n\n{picked[0]['text'][:1200]}", 
                            sys="You generate only the single best title in Korean. No quotes.", temperature=0.8)
        except Exception:
            title = f"{sector['name']} 동향 리뷰"
        article = make_article(sector, picked)
        path = save_post(sector, title, article)
        written.append(str(path))
        print("saved:", path)
    print(f"Generated {len(written)} post(s).")

if __name__ == "__main__":
    main()

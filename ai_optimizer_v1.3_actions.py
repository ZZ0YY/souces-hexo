# =================================================================================
# AI Optimizer & Hexo Formatter v1.3 (GitHub Actions, Stateful)
# v1.3 更新：集成SQLite数据库实现持久化，确保任务可中断和续行。
# =================================================================================
import os, re, json, shutil
from datetime import datetime
from database import initialize_database, add_processed_article, get_processed_articles
try:
    from bs4 import BeautifulSoup
    import html2text
    import google.generativeai as genai
except ImportError as e:
    print(f"[严重错误] 缺少必要的库: {e.name}\n请运行: pip install {e.name}")
    exit()

PUSHPLUS_TOKEN = os.getenv('PUSHPLUS_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
RAW_DATA_PARENT_FOLDER = "South-Plus-Raw-Data"
FINAL_OUTPUT_PARENT_FOLDER = "South-Plus-Articles"
PUSHPLUS_URL = 'http://www.pushplus.plus/send'
REPORTING_BATCH_SIZE = 50

def send_pushplus_notification(title, content):
    if not PUSHPLUS_TOKEN:
        print(f"[通知] PushPlus Token未配置，跳过发送。\n--- {title} ---\n{content}\n--------------------")
        return
    try:
        data = {"token": PUSHPLUS_TOKEN, "title": title, "content": content.replace('\n', '<br>'), "template": "html"}
        response = requests.post(PUSHPLUS_URL, json=data)
        if response.json()['code'] == 200:
            print(f"[通知] 成功发送到PushPlus: {title}")
        else:
            print(f"[通知错误] PushPlus发送失败: {response.text}")
    except Exception as e:
        print(f"[通知错误] 发送PushPlus通知时出现异常: {e}")

def add_alt_tags_to_html(html_content, seo_title):
    soup = BeautifulSoup(html_content, 'html.parser')
    img_tags = soup.find_all('img')
    if not img_tags: return html_content
    for i, img in enumerate(img_tags, 1):
        if not img.get('alt', '').strip():
            img['alt'] = f"{seo_title} - 图{i}"
    return str(soup)

def generate_seo_with_ai(model, original_title, content_text_snippet, author):
    if not model: return None
    prompt = f"""你是一名专业的游戏博客SEO专家。基于以下信息，生成SEO元数据。
**原始标题:** "{original_title}"
**作者:** "{author}"
**内容摘要:** "{content_text_snippet}..."
**任务:** 严格按以下JSON格式返回，不要有额外解释。
{{
  "seo_title": "一个50-70字符的吸引人的SEO标题。",
  "description": "一段120-160字符的Meta描述。",
  "tags": ["5到8个相关标签的数组", "例如 '视觉小说', '恋爱模拟'"]
}}"""
    try:
        response = model.generate_content(prompt, request_options={"timeout": 60})
        cleaned_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        result = json.loads(cleaned_text)
        if 'seo_title' in result and 'description' in result and 'tags' in result:
            return result
        return None
    except Exception as e:
        print(f"      [AI错误] 调用Gemini API失败: {e.__class__.__name__}")
        return None

def sanitize_filename(filename):
    safe_name = re.sub(r'[\\/*?:"<>|]', "", filename).strip()
    return safe_name[:150]

def create_seo_title(original_title, limit=70):
    cleaned_title = re.sub(r'【.*?】|\[.*?\]', '', original_title)
    cleaned_title = re.sub(r'\s*[\d\.]+G[B|]?.*$', '', cleaned_title, flags=re.IGNORECASE).strip()
    if not cleaned_title: cleaned_title = original_title
    if len(cleaned_title) > limit:
        shortened_at = cleaned_title.rfind(' ', 0, limit - 3)
        return cleaned_title[:shortened_at] + "..." if shortened_at != -1 else cleaned_title[:limit - 3] + "..."
    return cleaned_title

def create_meta_description(html_content, markdown_text, limit=160):
    text = re.sub(r'<.*?>|#+\s*|---\s*|\*\*|__', '', markdown_text)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    text = ' '.join(re.sub(r'!\[.*?\]\(.*?\)|{%.*?%}', '', text).split())
    if len(text) > limit:
        text = text[:limit].rsplit(' ', 1)[0] + '...'
    return text.replace('"', '“')

def extract_tags_from_title(original_title, author):
    tags = re.findall(r'【(.*?)】|\[(.*?)\]', original_title)
    flat_tags = [item for tpl in tags for item in tpl if item]
    blacklist = ['自购', '新汉化作品', '合集', 'bd', 'od', 'bo']
    cleaned_tags = {tag.strip() for tag in flat_tags if tag.strip().lower() not in blacklist and not re.match(r'^\d+(\.\d+)?(gb|g|mb)$', tag.strip(), re.IGNORECASE)}
    cleaned_tags.update(["汉化", "GALGAME"])
    if author != "未知作者": cleaned_tags.add(author)
    return list(cleaned_tags)

def generate_structured_data(seo_title, original_title, cover_url, description, hexo_date, author_name, source_url):
    schema = {"@context": "https://schema.org", "@type": "VideoGame", "name": seo_title, "alternateName": original_title, "description": description, "image": cover_url, "datePublished": hexo_date, "author": {"@type": "Person", "name": author_name}, "operatingSystem": "Windows", "applicationCategory": "GameApplication", "url": source_url}
    schema = {k: v for k, v in schema.items() if v}
    return f'<script type="application/ld+json">\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n</script>'

def get_existing_processed_titles(parent_folder):
    existing_titles = set()
    if not os.path.exists(parent_folder): return existing_titles
    for root, _, files in os.walk(parent_folder):
        for name in files:
            if name.endswith(".md"): existing_titles.add(os.path.splitext(name)[0])
    return existing_titles

def process_article_from_json(article_folder_path, md_converter, output_folders, gemini_model):
    json_path = os.path.join(article_folder_path, 'data.json')
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {'status': 'error', 'title': os.path.basename(article_folder_path), 'reason': f"无法读取JSON: {e}"}

    original_title = data['original_title']
    print(f"  -> 开始优化: {original_title[:50]}...")
    content_html = data['content_html']
    initial_md_for_meta = md_converter.handle(content_html)

    ai_results = None
    if gemini_model:
        text_snippet = re.sub(r'\s+', ' ', initial_md_for_meta).replace('#', '').replace('*', '').replace('-', '')[:500]
        ai_results = generate_seo_with_ai(gemini_model, original_title, text_snippet, data['author'])

    ai_used = False
    if ai_results:
        seo_title, description, tags = ai_results['seo_title'], ai_results['description'], ai_results['tags']
        ai_used = True
        tags.extend(["汉化", "GALGAME"])
        if data['author'] != "未知作者": tags.append(data['author'])
        tags = sorted(list(set(tags)))
    else:
        seo_title = create_seo_title(original_title)
        description = create_meta_description(content_html, initial_md_for_meta)
        tags = extract_tags_from_title(original_title, data['author'])

    safe_filename = sanitize_filename(seo_title)
    if safe_filename in get_existing_processed_titles(FINAL_OUTPUT_PARENT_FOLDER):
        add_processed_article(article_folder_path)
        return {'status': 'skipped', 'title': seo_title}

    html_with_alts = add_alt_tags_to_html(content_html, seo_title)
    content_md = md_converter.handle(html_with_alts)
    
    front_matter_parts = ['---', f'title: "{original_title}"', f'seo_title: "{seo_title}"']
    if data.get("cover_image_url"): front_matter_parts.append(f'cover: "{data.get("cover_image_url")}"')
    front_matter_parts.extend([f'description: "{description}"', 'categories:', '  - 资源贴', 'tags:'] + [f'  - {tag}' for tag in tags] + [f'date: {data["hexo_date"]}', '---'])
    front_matter = "\n".join(front_matter_parts) + "\n\n"
    
    body_header = f"# {original_title}\n\n**{data['author']}** - {data['publish_date']}\n\n"
    source_footer = f"\n\n---\n\n**Source:** [{original_title}]({data['source_url']})\n"
    structured_data = generate_structured_data(seo_title, original_title, data.get('cover_image_url'), description, data['hexo_date'], data['author'], data['source_url'])
    
    final_content_remote = front_matter + body_header + content_md + source_footer + structured_data
    with open(os.path.join(output_folders['remote'], f"{safe_filename}.md"), 'w', encoding='utf-8') as f:
        f.write(final_content_remote)
    
    local_asset_folder = os.path.join(output_folders['local'], safe_filename)
    os.makedirs(local_asset_folder, exist_ok=True)
    soup_for_local = BeautifulSoup(content_html, 'html.parser')
    
    if not soup_for_local.find_all('img') or not os.path.exists(os.path.join(article_folder_path, 'images')):
        shutil.copy(os.path.join(output_folders['remote'], f"{safe_filename}.md"), os.path.join(output_folders['local'], f"{safe_filename}.md"))
    else:
        for i, img in enumerate(soup_for_local.find_all('img'), 1):
            original_src = img.get('src')
            if original_src and original_src.startswith('images/'):
                img_filename = os.path.basename(original_src)
                source_image_path = os.path.join(article_folder_path, 'images', img_filename)
                if os.path.exists(source_image_path):
                    shutil.copy(source_image_path, local_asset_folder)
                    alt_text = f'{seo_title} - 图{i}'.replace('"', '\\"')
                    img.replace_with(BeautifulSoup(f'{{% asset_img {img_filename} "{alt_text}" %}}', 'html.parser'))
        
        final_content_local = front_matter + body_header + md_converter.handle(str(soup_for_local)) + source_footer + structured_data
        with open(os.path.join(output_folders['local'], f"{safe_filename}.md"), 'w', encoding='utf-8') as f:
            f.write(final_content_local)
    
    add_processed_article(article_folder_path)
    return {'status': 'success_ai' if ai_used else 'success_local', 'title': seo_title}


def main():
    send_pushplus_notification("South-Plus优化器任务开始", f"任务于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 启动。")
    if not os.path.exists(RAW_DATA_PARENT_FOLDER):
        send_pushplus_notification("优化器任务警告", f"'{RAW_DATA_PARENT_FOLDER}' 不存在，任务跳过。")
        return

    initialize_database()
    already_processed = get_processed_articles()

    gemini_model = None
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY_HERE':
        send_pushplus_notification("优化器任务警告", "GEMINI_API_KEY 未配置，将使用本地规则。")
    else:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            gemini_model = genai.GenerativeModel('gemini-pro')
            send_pushplus_notification("Debug: AI状态", "Gemini模型已加载。")
        except Exception as e:
            send_pushplus_notification("优化器任务严重错误", f"Gemini初始化失败: {e}，将使用本地规则。")

    all_articles_to_process = []
    for date_folder in sorted(os.listdir(RAW_DATA_PARENT_FOLDER)):
        date_path = os.path.join(RAW_DATA_PARENT_FOLDER, date_folder)
        if os.path.isdir(date_path):
            for article_folder in os.listdir(date_path):
                article_path = os.path.join(date_path, article_folder)
                if os.path.isdir(article_path) and article_path not in already_processed:
                    all_articles_to_process.append({'path': article_path, 'date': date_folder})

    if not all_articles_to_process:
        send_pushplus_notification("优化器任务完成", "没有发现任何新的原始数据需要处理。")
        return

    print(f"发现 {len(all_articles_to_process)} 篇新文章待优化。")
    md_converter = html2text.HTML2Text(bodywidth=0)
    md_converter.links_each_paragraph = True
    
    processed_count = 0
    results = []
    batch_results = []
    
    for item in all_articles_to_process:
        processed_count += 1
        date_folder_name = item['date']
        article_path = item['path']
        output_remote = os.path.join(FINAL_OUTPUT_PARENT_FOLDER, date_folder_name)
        output_local = os.path.join(FINAL_OUTPUT_PARENT_FOLDER, f"{date_folder_name}_local")
        os.makedirs(output_remote, exist_ok=True)
        os.makedirs(output_local, exist_ok=True)
        output_folders = {'remote': output_remote, 'local': output_local}
        
        result = process_article_from_json(article_path, md_converter, output_folders, gemini_model)
        results.append(result)
        batch_results.append(result)

        if processed_count % REPORTING_BATCH_SIZE == 0 or processed_count == len(all_articles_to_process):
            ai = sum(1 for r in batch_results if r['status'] == 'success_ai')
            local = sum(1 for r in batch_results if r['status'] == 'success_local')
            error = sum(1 for r in batch_results if r['status'] == 'error')
            skipped = sum(1 for r in batch_results if r['status'] == 'skipped')
            summary = f"批次进度 ({processed_count}/{len(all_articles_to_process)}):\n- AI优化: {ai}\n- 本地规则: {local}\n- 跳过: {skipped}\n- 失败: {error}"
            send_pushplus_notification(f"优化器进度报告 ({processed_count}/{len(all_articles_to_process)})", summary)
            batch_results = []

    ai_total = sum(1 for r in results if r['status'] == 'success_ai')
    local_total = sum(1 for r in results if r['status'] == 'success_local')
    error_total = sum(1 for r in results if r['status'] == 'error')
    skipped_total = sum(1 for r in results if r['status'] == 'skipped')
    final_summary = f"所有优化任务已完成。\n\n任务总数: {len(all_articles_to_process)}\n- 🤖 AI优化成功: {ai_total}\n- ⚙️ 本地规则成功: {local_total}\n- ⏭️ 已存在跳过: {skipped_total}\n- ❌ 处理失败: {error_total}"
    send_pushplus_notification("优化器任务最终总结", final_summary)
    
if __name__ == "__main__":
    main()

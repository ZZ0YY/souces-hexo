# =================================================================================
# South-Plus to JSON Data Scraper v12.0 (GitHub Actions, Stateful)
# v12.0 更新：集成SQLite数据库实现持久化，确保任务可中断和续行。
# =================================================================================
import os, re, time, json
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from database import initialize_database, add_scraped_article, get_scraped_articles
try:
    import requests
    from bs4 import BeautifulSoup
    import concurrent.futures
except ImportError as e:
    print(f"[严重错误] 缺少必要的库: {e.name}\n请运行: pip install {e.name}")
    exit()

PUSHPLUS_TOKEN = os.getenv('PUSHPLUS_TOKEN')
SPLUS_COOKIE = os.getenv('SPLUS_COOKIE')
FID = os.getenv('FID', '19')
MAX_THREADS = int(os.getenv('MAX_THREADS', '5'))
OUTPUT_PARENT_FOLDER = "South-Plus-Raw-Data"
BASE_URL = "https://www.south-plus.net/"
HEADERS = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0' }
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

def sanitize_filename(filename):
    safe_name = re.sub(r'[\\/*?:"<>|]', "", filename).strip()
    return safe_name[:150]

def get_existing_article_folders(parent_folder):
    existing_folders = set()
    if not os.path.exists(parent_folder):
        return existing_folders
    for date_folder in os.listdir(parent_folder):
        date_path = os.path.join(parent_folder, date_folder)
        if os.path.isdir(date_path):
            for article_folder in os.listdir(date_path):
                existing_folders.add(article_folder)
    return existing_folders

def parse_raw_cookie_string(cookie_string):
    cookies = {}
    if not cookie_string: return cookies
    try:
        for item in cookie_string.split(';'):
            item = item.strip()
            if not item: continue
            key, value = item.split('=', 1)
            cookies[key.strip()] = value.strip()
    except Exception as e:
        print(f"[错误] 解析Cookie字符串失败: {e}")
    return cookies

def download_images_and_update_html(html_content, asset_folder, session, article_title):
    soup = BeautifulSoup(html_content, 'html.parser')
    img_tags = soup.find_all('img')
    if not img_tags: return html_content, []
    print(f"      - {article_title[:15]}...: 本地化 {len(img_tags)} 张图片...")
    os.makedirs(asset_folder, exist_ok=True)
    failed_images = []
    for i, img in enumerate(img_tags, 1):
        original_url = img.get('src')
        if not original_url: continue
        full_url = urljoin(BASE_URL, original_url) if not original_url.startswith(('http://', 'https://')) else original_url
        try:
            _, ext = os.path.splitext(os.path.basename(urlparse(full_url).path))
            if not ext: ext = ".jpg"
            safe_filename = f"image_{i}{ext}"
            local_filepath = os.path.join(asset_folder, safe_filename)
            img_response = session.get(full_url, timeout=30, stream=True)
            img_response.raise_for_status()
            with open(local_filepath, 'wb') as f:
                for chunk in img_response.iter_content(chunk_size=8192): f.write(chunk)
            img['src'] = f"images/{safe_filename}"
        except Exception as e:
            failed_images.append({'url': full_url, 'reason': f"下载失败: {e.__class__.__name__}"})
    return str(soup), failed_images

def get_full_article_details(session, url):
    for attempt in range(3):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')
            if buy_button := soup.select_one("input[onclick*='job.php?action=buytopic']"):
                buy_url_full = urljoin(BASE_URL, buy_button['onclick'].split("'")[1])
                send_pushplus_notification("Debug: 购买文章", f"正在尝试购买文章：{url}")
                session.get(buy_url_full, timeout=20).raise_for_status()
                response = session.get(url, timeout=20)
                response.raise_for_status()
                response.encoding = 'utf-8'
                soup = BeautifulSoup(response.text, 'html.parser')

            content_div = soup.find('div', id='read_tpc')
            if not content_div or not content_div.get_text(strip=True):
                return {'error': "帖子内容为空或无法解析"}

            content_html = str(content_div)
            author_tag = soup.select_one('th.r_two strong')
            author = author_tag.get_text(strip=True) if author_tag else '未知作者'
            date_tag = soup.select_one('div.tiptop span.fl.gray')
            post_date_str = date_tag.get_text(strip=True) if date_tag else datetime.now().strftime('%Y-%m-%d %H:%M')
            cover_image_url = None
            if content_div and (first_img_tag := content_div.find('img')) and first_img_tag.get('src'):
                cover_image_url = urljoin(url, first_img_tag['src'])
            return {'content_html': content_html, 'author': author, 'post_date': post_date_str, 'cover_image_url': cover_image_url}
        except Exception as e:
            if attempt < 2:
                time.sleep(30)
            else:
                return {'error': f"访问详情页失败: {e.__class__.__name__}"}
    return {'error': "访问详情页在多次重试后仍然失败"}

def process_single_article(article_info, session, output_today_folder):
    original_title = article_info['title']
    safe_foldername = sanitize_filename(original_title)
    print(f"-> 开始处理: {original_title[:50]}...")
    
    full_url = urljoin(BASE_URL, article_info['row'].select_one('a[id^="a_ajax_"]')['href'])
    details = get_full_article_details(session, full_url)
    
    if details and 'error' not in details:
        article_output_path = os.path.join(output_today_folder, safe_foldername)
        os.makedirs(article_output_path, exist_ok=True)
        content_html, failed_images = download_images_and_update_html(details['content_html'], os.path.join(article_output_path, 'images'), session, original_title)
        
        hexo_date_str = f"{datetime.now().strftime('%Y-%m-%d %H:%M')}:00"
        try:
            if article_info.get('date_str'):
                hexo_date_str = datetime.strptime(article_info['date_str'], '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            pass # Use default on error

        data_to_save = {
            "original_title": original_title, "source_url": full_url, "author": details['author'], 
            "publish_date": details['post_date'], "scrape_date_utc": datetime.now(timezone.utc).isoformat(), 
            "cover_image_url": details['cover_image_url'], "content_html": content_html, "hexo_date": hexo_date_str
        }
        
        try:
            with open(os.path.join(article_output_path, 'data.json'), 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
            add_scraped_article(safe_foldername) # 成功后写入数据库
            print(f"  √ 数据已保存并记录: {safe_foldername[:30]}...")
        except IOError as e:
            return {'status': 'error', 'title': original_title, 'reason': f"保存JSON失败: {e}"}
        
        return {'status': 'partial_success' if failed_images else 'success', 'title': original_title}
    
    return {'status': 'error', 'title': original_title, 'reason': details.get('error', '未知详情页错误')}

def main():
    start_time_total = time.time()
    send_pushplus_notification("South-Plus爬虫任务开始", f"任务于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 启动。")
    if not SPLUS_COOKIE:
        send_pushplus_notification("爬虫任务严重错误", "SPLUS_COOKIE 未配置！")
        exit(1)

    initialize_database()
    already_scraped = get_scraped_articles()
    
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.update(parse_raw_cookie_string(SPLUS_COOKIE))
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    output_today_folder = os.path.join(OUTPUT_PARENT_FOLDER, today_str)
    os.makedirs(output_today_folder, exist_ok=True)
    
    existing_folders = get_existing_article_folders(OUTPUT_PARENT_FOLDER)

    try:
        print("正在检测总页数...")
        first_page_url = f"{BASE_URL}thread.php?fid-{FID}.html"
        response = session.get(first_page_url, timeout=30)
        response.raise_for_status()
        pages_tag = BeautifulSoup(response.text, 'html.parser').select_one('li.pagesone')
        match = re.search(r'(\d+)/(\d+)', pages_tag.text if pages_tag else "1/1")
        total_pages = int(match.group(2))
        print(f"检测到共 {total_pages} 页。已在数据库记录 {len(already_scraped)} 篇。")
    except Exception as e:
        send_pushplus_notification("爬虫任务错误", f"检测总页数失败: {e}")
        exit(1)

    all_new_articles = []
    for page_num in range(1, total_pages + 1):
        print(f"扫描第 {page_num}/{total_pages} 页...")
        time.sleep(1)
        try:
            page_url = f"{BASE_URL}thread.php?fid-{FID}-page-{page_num}.html" if page_num > 1 else first_page_url
            response = session.get(page_url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            separator_td = soup.find('td', string=re.compile(r'普通主题'))
            normal_thread_rows = separator_td.find_parent('tr').find_next_siblings('tr') if separator_td else soup.select('tr.tr3.t_one')
            for row in normal_thread_rows:
                if (link_tag := row.select_one('a[id^="a_ajax_"]')):
                    title = link_tag.get_text(strip=True)
                    safe_folder = sanitize_filename(title)
                    if safe_folder not in existing_folders and safe_folder not in already_scraped:
                        date_tag = row.select_one('td.author em span') or row.select_one('td.author em')
                        date_str = date_tag.get_text(strip=True) if date_tag else None
                        all_new_articles.append({'title': title, 'row': row, 'date_str': date_str})
        except Exception as e:
            print(f"  [错误] 访问页面 {page_num} 失败: {e}")
    
    if not all_new_articles:
        send_pushplus_notification("爬虫任务完成", "没有发现任何需要处理的新文章。")
        return

    send_pushplus_notification("Debug: 发现新文章", f"扫描完成，共发现 {len(all_new_articles)} 篇新文章待抓取。")
    
    results = []
    batch_results = []
    processed_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = [executor.submit(process_single_article, info, session, output_today_folder) for info in all_new_articles]
        for future in concurrent.futures.as_completed(futures):
            processed_count += 1
            if result := future.result():
                results.append(result)
                batch_results.append(result)
            
            if processed_count % REPORTING_BATCH_SIZE == 0 or processed_count == len(all_new_articles):
                success = sum(1 for r in batch_results if r['status'] == 'success')
                partial = sum(1 for r in batch_results if r['status'] == 'partial_success')
                error = sum(1 for r in batch_results if r['status'] == 'error')
                summary = f"批次进度 ({processed_count}/{len(all_new_articles)}):\n- 成功: {success}\n- 部分成功: {partial}\n- 失败: {error}"
                send_pushplus_notification(f"爬虫进度报告 ({processed_count}/{len(all_new_articles)})", summary)
                batch_results = []
    
    total_success = sum(1 for r in results if r['status'] == 'success')
    total_partial = sum(1 for r in results if r['status'] == 'partial_success')
    total_error = sum(1 for r in results if r['status'] == 'error')
    elapsed = time.time() - start_time_total
    final_summary = f"所有爬取任务已完成。\n\n总耗时: {elapsed:.2f} 秒\n任务总数: {len(all_new_articles)}\n- ✅ 完全成功: {total_success}\n- ⚠️ 部分成功: {total_partial}\n- ❌ 完全失败: {total_error}"
    send_pushplus_notification("爬虫任务最终总结", final_summary)
    
if __name__ == "__main__":
    main()

# database.py
import sqlite3

DB_FILE = "progress.db"

def initialize_database():
    """初始化数据库，创建必要的表（如果不存在）。"""
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        # 存储已成功抓取的文章的唯一标识（文件夹名）
        cur.execute('''
            CREATE TABLE IF NOT EXISTS scraped_articles (
                folder_name TEXT PRIMARY KEY
            )
        ''')
        # 存储已成功优化处理的原始数据文件夹的路径
        cur.execute('''
            CREATE TABLE IF NOT EXISTS processed_articles (
                folder_path TEXT PRIMARY KEY
            )
        ''')
        con.commit()

def add_scraped_article(folder_name):
    """记录一篇已成功抓取的文章。"""
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("INSERT OR IGNORE INTO scraped_articles (folder_name) VALUES (?)", (folder_name,))
            con.commit()
    except sqlite3.Error as e:
        print(f"[DB错误] 写入scraped_articles失败: {e}")

def get_scraped_articles():
    """获取所有已抓取文章的文件夹名集合，用于快速查找。"""
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("SELECT folder_name FROM scraped_articles")
            return {row[0] for row in cur.fetchall()}
    except sqlite3.Error as e:
        print(f"[DB错误] 读取scraped_articles失败: {e}")
        return set()

def add_processed_article(folder_path):
    """记录一个已成功优化的原始数据文件夹。"""
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("INSERT OR IGNORE INTO processed_articles (folder_path) VALUES (?)", (folder_path,))
            con.commit()
    except sqlite3.Error as e:
        print(f"[DB错误] 写入processed_articles失败: {e}")


def get_processed_articles():
    """获取所有已优化文件夹的路径集合。"""
    try:
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute("SELECT folder_path FROM processed_articles")
            return {row[0] for row in cur.fetchall()}
    except sqlite3.Error as e:
        print(f"[DB错误] 读取processed_articles失败: {e}")
        return set()

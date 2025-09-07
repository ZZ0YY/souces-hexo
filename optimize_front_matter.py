# -*- coding: utf-8 -*-

# =================================================================================
# Hexo Front Matter AI Optimizer v2.0 (Stateful, Batch Processing)
#
# 功能：
#   - 状态感知：自动对比输入和输出目录，只处理尚未被优化过的新增文章。
#   - 批量处理：每次运行时，最多只处理指定数量（BATCH_SIZE）的文章，便于分批创建PR进行审核。
#   - 目录镜像：完整保留原始文章的目录结构（如日期、本地/远程版子文件夹）。
#   - 专注内容：只处理 Markdown (.md) 文件，绝不复制图片或其它资产文件。
#   - AI 模型：使用高效的 gemini-2.5-flash-lite 模型，并禁用思考功能以降低成本。
# =================================================================================

import os
import re
import json
import time
import google.generativeai as genai
from google.generativeai import types # 引入 types 以便配置模型

# --- 用户配置 ---

# 从环境变量中读取 Gemini API 密钥
API_KEY = os.getenv("GEMINI_API_KEY")

# 定义输入文件夹，即存放你原始 Hexo Markdown 文章的地方。
INPUT_FOLDER = "South-Plus-Articles"  # <--- 你的原始文章文件夹

# 定义输出文件夹，用于存放经 AI 优化后生成的新文章。
OUTPUT_FOLDER = "ai-optimized-articles" # <--- 你希望保存优化后文章的文件夹

# 定义每个批次处理的文章数量
BATCH_SIZE = 30

# --- AI 与模型配置 ---

def configure_gemini():
    """配置并验证Gemini API。"""
    if not API_KEY:
        print("[严重错误] 未找到 GEMINI_API_KEY 环境变量。请在 GitHub Secrets 中设置它。")
        exit(1)
    try:
        genai.configure(api_key=API_KEY)
        print("[信息] Gemini API 配置成功。")
    except Exception as e:
        print(f"[严重错误] Gemini API 配置失败: {e}")
        exit(1)

def generate_metadata_with_gemini(content: str):
    """
    调用 Gemini API，根据文章内容生成元数据。
    """
    # 【模型更新】切换到 gemini-2.5-flash-lite 模型
    model = genai.GenerativeModel('models/gemini-2.5-flash-lite')

    # 【配置更新】创建一个生成配置，明确禁用思考功能
    generation_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    )

    prompt = f"""
    你是一名专业的SEO编辑和博客内容分析师。你的任务是根据下面提供的文章正文，生成优化的元数据（metadata）。
    请严格按照以下JSON格式返回结果，不要包含任何额外的解释或Markdown的代码块标记。

    {{
      "title": "一个引人入胜、信息丰富、符合原文主旨的中文标题",
      "seo_title": "一个为搜索引擎优化的、更简短的中文标题（建议60个汉字以内）",
      "description": "一段吸引人的元描述（meta description），精准概括文章核心内容，用于搜索结果展示（建议150个汉字以内）",
      "categories": ["文章的主要分类（通常只有一个）"],
      "tags": ["5到8个最相关的关键词标签（列表形式）"]
    }}

    ---
    [文章正文内容开始]
    {content[:8000]}
    [文章正文内容结束]
    ---
    """
    
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    try:
        # 【调用更新】在API调用时传入新的配置
        response = model.generate_content(
            prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        metadata = json.loads(cleaned_text)
        return metadata

    except json.JSONDecodeError:
        print(f"  [AI错误] AI返回的不是有效的JSON格式。返回内容:\n{response.text}")
        return None
    except Exception as e:
        print(f"  [AI错误] 调用Gemini API时发生未知错误: {e}")
        return None

def process_file(filepath: str, relative_path: str, output_dir: str):
    """
    处理单个 Markdown 文件：读取、调用AI、重写。
    """
    print(f"\n[处理中] -> {relative_path}")

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            full_content = f.read()
    except Exception as e:
        print(f"  [文件错误] 无法读取文件: {e}")
        return

    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', full_content, re.DOTALL)
    body_content = match.group(2).strip() if match else full_content

    if not body_content:
        print("  [警告] 文章正文内容为空，已跳过。")
        return

    print("  - 正在调用 AI 生成元数据...")
    new_metadata = generate_metadata_with_gemini(body_content)

    if not new_metadata:
        print("  [失败] 未能从 AI 获取有效的元数据，已跳过此文件。")
        return
    
    print("  - 成功获取 AI 元数据，正在构建新文件...")
    
    title = new_metadata.get('title', 'AI Generated Title').replace('"', '\\"')
    seo_title = new_metadata.get('seo_title', '').replace('"', '\\"')
    description = new_metadata.get('description', '').replace('"', '\\"')
    categories = new_metadata.get('categories', [])
    tags = new_metadata.get('tags', [])

    new_front_matter_lines = [
        "---",
        f'title: "{title}"',
        f'seo_title: "{seo_title}"',
        f'description: "{description}"'
    ]
    if categories:
        new_front_matter_lines.append("categories:")
        for category in categories:
            new_front_matter_lines.append(f"  - {category}")
    if tags:
        new_front_matter_lines.append("tags:")
        for tag in tags:
            new_front_matter_lines.append(f"  - {tag}")
    new_front_matter_lines.append("---")
    
    new_front_matter = "\n".join(new_front_matter_lines)
    new_full_content = f"{new_front_matter}\n\n{body_content}"

    # 【目录镜像逻辑】根据相对路径构建完整的输出路径
    output_filepath = os.path.join(output_dir, relative_path)
    
    # 确保输出路径中的子文件夹存在
    os.makedirs(os.path.dirname(output_filepath), exist_ok=True)
    
    try:
        with open(output_filepath, 'w', encoding='utf-8') as f:
            f.write(new_full_content)
        print(f"  [成功] 已将优化后的文件保存至: {output_filepath}")
    except Exception as e:
        print(f"  [文件错误] 无法写入新文件: {e}")

def find_unprocessed_files(input_dir: str, output_dir: str):
    """对比输入和输出目录，返回尚未处理的文件列表。"""
    source_files = set()
    for root, _, files in os.walk(input_dir):
        for file in files:
            if file.endswith(".md"):
                # 计算从输入目录根开始的相对路径
                relative_path = os.path.relpath(os.path.join(root, file), input_dir)
                source_files.add(relative_path)

    processed_files = set()
    if os.path.exists(output_dir):
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith(".md"):
                    relative_path = os.path.relpath(os.path.join(root, file), output_dir)
                    processed_files.add(relative_path)

    # 返回差集，即源目录中有但目标目录中没有的文件
    unprocessed_relative_paths = sorted(list(source_files - processed_files))
    return unprocessed_relative_paths

def main():
    """主执行函数"""
    print("="*60)
    print("Hexo Front Matter AI 优化脚本 v2.0 启动")
    print(f"批次大小: {BATCH_SIZE} 篇")
    print("="*60)

    configure_gemini()

    if not os.path.exists(INPUT_FOLDER):
        print(f"[严重错误] 输入文件夹 '{INPUT_FOLDER}' 不存在。")
        exit(1)
        
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # 【状态感知逻辑】
    print("[信息] 正在对比源文件夹和目标文件夹，寻找未处理的文章...")
    unprocessed_paths = find_unprocessed_files(INPUT_FOLDER, OUTPUT_FOLDER)

    if not unprocessed_paths:
        print("\n[完成] 所有文章均已处理！无需执行任何操作。")
        print("="*60)
        return

    print(f"[信息] 发现 {len(unprocessed_paths)} 篇未处理的文章。")
    
    # 【批量处理逻辑】
    files_to_process_this_run = unprocessed_paths[:BATCH_SIZE]
    print(f"[信息] 本次运行将处理 {len(files_to_process_this_run)} 篇文章（一个批次）。")
    
    for relative_path in files_to_process_this_run:
        full_input_path = os.path.join(INPUT_FOLDER, relative_path)
        process_file(full_input_path, relative_path, OUTPUT_FOLDER)
        time.sleep(2) # 礼貌性延时，避免API速率限制

    remaining_count = len(unprocessed_paths) - len(files_to_process_this_run)
    print("\n" + "="*60)
    print("本批次任务已完成！")
    if remaining_count > 0:
        print(f"仍有 {remaining_count} 篇文章等待处理。请在合并此 PR 后，再次运行工作流以处理下一批。")
    else:
        print("所有文章均已处理完毕！")
    print("="*60)

if __name__ == "__main__":
    main()

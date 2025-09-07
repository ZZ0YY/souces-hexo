# -*- coding: utf-8 -*-

# =================================================================================
# Hexo Front Matter AI Optimizer v2.2 (Safety Settings Fix)
#
# 更新日志：
#   v2.2: 在API调用中添加 safety_settings，将所有安全审查阈值设为 BLOCK_NONE，
#         以解决因 PROHIBITED_CONTENT 导致请求被阻止的问题。
#   v2.1: 修正了 GenerationConfig 的类名错误。
#
# 功能：
#   - 状态感知、批量处理、目录镜像、专注内容、高效AI模型。
# =================================================================================

import os
import re
import json
import time
import google.generativeai as genai

# --- 用户配置 ---
API_KEY = os.getenv("GEMINI_API_KEY")
INPUT_FOLDER = "South-Plus-Articles"
OUTPUT_FOLDER = "ai-optimized-articles"
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
    model = genai.GenerativeModel('models/gemini-2.5-flash-lite')
    generation_config = genai.GenerationConfig()

    # ==============================================================================
    # 【已修正】
    # 添加 safety_settings 来放宽内容审查策略。
    # 这可以防止因文章内容（如 GALGAME 相关术语）触发安全机制而导致请求失败。
    # ==============================================================================
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    prompt = f"""
    你是一名专业的SEO编辑和博客内容分析师。你的任务是根据下面提供的文章正文，生成优化的元数据（metadata）。
    请严格按照以下JSON格式返回结果，不要包含任何额外的解释或Markdown的代码块标记。

    {{
      "title": "一个引人入胜、信息丰富、符合原文主旨的中文标题",
      "seo_title": "一个为搜索引擎优化的、更简短的中文标题（建议60个汉字以内）",
      "description": "一段吸引人的元描述（meta description），精准概括文章核心内容，用于搜索结果展示（建议150个汉字以内）",
      "tags": ["5到8个最相关的关键词标签（列表形式）"],
      "categories": ["文章的主要分类（通常只有一个）"]
    }}

    ---
    [文章正文内容开始]
    {content[:8000]}
    [文章正文内容结束]
    ---
    """

    try:
        # 在API调用时传入 safety_settings
        response = model.generate_content(
            prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        # 在访问 response.text 之前，先检查是否有内容返回
        if not response.candidates:
             print(f"  [AI错误] AI返回了空结果。可能是被其他未知原因阻止。反馈: {response.prompt_feedback}")
             return None

        cleaned_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        metadata = json.loads(cleaned_text)
        return metadata

    except json.JSONDecodeError:
        print(f"  [AI错误] AI返回的不是有效的JSON格式。返回内容:\n{response.text}")
        return None
    # 捕获因空结果直接访问 .text 导致的 ValueError 或其他错误
    except (ValueError, Exception) as e:
        # 打印更详细的错误，包括 prompt_feedback
        try:
            feedback = response.prompt_feedback
            print(f"  [AI错误] 调用Gemini API时发生错误: {e}。安全反馈: {feedback}")
        except Exception:
            print(f"  [AI错误] 调用Gemini API时发生未知错误，且无法获取安全反馈: {e}")
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

    output_filepath = os.path.join(output_dir, relative_path)
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
                relative_path = os.path.relpath(os.path.join(root, file), input_dir)
                source_files.add(relative_path)

    processed_files = set()
    if os.path.exists(output_dir):
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith(".md"):
                    relative_path = os.path.relpath(os.path.join(root, file), output_dir)
                    processed_files.add(relative_path)

    unprocessed_relative_paths = sorted(list(source_files - processed_files))
    return unprocessed_relative_paths

def main():
    """主执行函数"""
    print("="*60)
    print("Hexo Front Matter AI 优化脚本 v2.2 启动")
    print(f"批次大小: {BATCH_SIZE} 篇")
    print("="*60)

    configure_gemini()

    if not os.path.exists(INPUT_FOLDER):
        print(f"[严重错误] 输入文件夹 '{INPUT_FOLDER}' 不存在。")
        exit(1)
        
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    print("[信息] 正在对比源文件夹和目标文件夹，寻找未处理的文章...")
    unprocessed_paths = find_unprocessed_files(INPUT_FOLDER, OUTPUT_FOLDER)

    if not unprocessed_paths:
        print("\n[完成] 所有文章均已处理！无需执行任何操作。")
        print("="*60)
        return

    print(f"[信息] 发现 {len(unprocessed_paths)} 篇未处理的文章。")
    
    files_to_process_this_run = unprocessed_paths[:BATCH_SIZE]
    print(f"[信息] 本次运行将处理 {len(files_to_process_this_run)} 篇文章（一个批次）。")
    
    for relative_path in files_to_process_this_run:
        full_input_path = os.path.join(INPUT_FOLDER, relative_path)
        process_file(full_input_path, relative_path, OUTPUT_FOLDER)
        time.sleep(2)

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
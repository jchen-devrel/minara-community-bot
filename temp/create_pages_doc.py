#!/usr/bin/env python3
"""
将 forum_posts.json + scored_requests.json 生成 Pages 文档，每页一条 Feature Request 卡片。

用法:
  python3 scripts/create_pages_doc.py
"""
import json
import subprocess
from pathlib import Path

FORUM_PATH = Path(__file__).resolve().parent.parent / "scripts" / "forum_export" / "forum_posts.json"
SCORED_PATH = Path(__file__).resolve().parent.parent / "scripts" / "forum_export" / "scored_requests.json"
OUTPUT_PATH = Path.home() / "Desktop" / "Feature_Requests.pages"


def main():
    with open(FORUM_PATH, encoding="utf-8") as f:
        posts = json.load(f)
    
    scores_map = {}
    if SCORED_PATH.is_file():
        with open(SCORED_PATH, encoding="utf-8") as f:
            scored = json.load(f)
        for s in scored:
            scores_map[s.get("title", "")] = s

    # Build AppleScript to create Pages document
    pages = []
    for i, post in enumerate(posts):
        title = post.get("title", "").replace('"', '\\"').replace("\\", "\\\\")
        author = post.get("author", "").replace('"', '\\"')
        tags = ", ".join(post.get("tags", [])) or "—"
        content = (post.get("content", "") or "").replace('"', '\\"').replace("\n", "\\n")[:500]
        
        score_data = scores_map.get(post.get("title", ""), {})
        overall = score_data.get("overall_score", "—")
        user_val = score_data.get("user_value", "—")
        biz = score_data.get("business_impact", "—")
        feas = score_data.get("feasibility", "—")
        verdict = score_data.get("verdict", "—")
        reason = score_data.get("reason_zh", "").replace('"', '\\"').replace("\n", " ")

        card_text = f"""Feature Request #{i+1}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{title}

Author: {author}
Tags: {tags}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{content[:500]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AI Score: {overall}/10
User Value: {user_val}  |  Business Impact: {biz}  |  Feasibility: {feas}
Verdict: {verdict}

{reason}"""
        pages.append(card_text)

    print(f"共 {len(pages)} 页")

    # Use AppleScript to create Pages document
    applescript = '''
tell application "Pages"
    activate
    set newDoc to make new document
    
'''
    for i, page_text in enumerate(pages):
        escaped = page_text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        if i == 0:
            applescript += f'    tell newDoc\n'
            applescript += f'        set body text of newDoc to "{escaped}"\n'
            applescript += f'    end tell\n'
        else:
            applescript += f'    tell newDoc\n'
            applescript += f'        set body text of newDoc to (body text of newDoc) & "\\n" & return & "{escaped}"\n'
            applescript += f'    end tell\n'

    applescript += '''
end tell
'''

    # AppleScript for Pages is limited. Use a simpler approach: generate RTF and open in Pages.
    print("生成 RTF 文档...")
    
    rtf_content = r"{\rtf1\ansi\deff0"
    rtf_content += r"{\fonttbl{\f0\fswiss\fcharset0 Helvetica;}{\f1\fmodern\fcharset0 Courier New;}}"
    rtf_content += r"{\colortbl;\red0\green0\blue0;\red33\green150\blue83;\red220\green53\blue69;\red255\green193\blue7;}"
    
    for i, post in enumerate(posts):
        title = post.get("title", "")
        author = post.get("author", "")
        tags = ", ".join(post.get("tags", [])) or "—"
        content = (post.get("content", "") or "")[:500]
        
        score_data = scores_map.get(title, {})
        overall = score_data.get("overall_score", "—")
        user_val = score_data.get("user_value", "—")
        biz = score_data.get("business_impact", "—")
        feas = score_data.get("feasibility", "—")
        verdict = score_data.get("verdict", "—")
        reason = score_data.get("reason_zh", "")

        verdict_color = r"\cf2" if verdict == "worth_it" else (r"\cf3" if verdict == "not_worth_it" else r"\cf4")

        def rtf_escape(s):
            out = []
            for ch in s:
                cp = ord(ch)
                if cp > 127:
                    out.append(f"\\u{cp}?")
                elif ch == '\\':
                    out.append('\\\\')
                elif ch == '{':
                    out.append('\\{')
                elif ch == '}':
                    out.append('\\}')
                elif ch == '\n':
                    out.append('\\line ')
                else:
                    out.append(ch)
            return "".join(out)

        if i > 0:
            rtf_content += r"\page "

        rtf_content += (
            r"\pard\qc\f0\b\fs48 " + rtf_escape(f"Feature Request #{i+1}") + r"\b0\par"
            r"\pard\qc\fs20\cf0 " + r"\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_" + r"\par\par"
            r"\pard\ql\f0\b\fs36 " + rtf_escape(title) + r"\b0\par\par"
            r"\fs24 Author: " + rtf_escape(author) + r"\par"
            r"Tags: " + rtf_escape(tags) + r"\par\par"
            r"\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\par\par"
            r"\fs22 " + rtf_escape(content) + r"\par\par"
            r"\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\par\par"
            r"\b\fs28 AI Score: " + str(overall) + r"/10\b0\par"
            r"\fs22 User Value: " + str(user_val) + r"  |  Business Impact: " + str(biz) + r"  |  Feasibility: " + str(feas) + r"\par"
            r"\b " + verdict_color + r" Verdict: " + rtf_escape(str(verdict)) + r"\cf0\b0\par\par"
            r"\i\fs22 " + rtf_escape(reason) + r"\i0\par"
        )

    rtf_content += r"}"

    rtf_path = Path.home() / "Desktop" / "Feature_Requests.rtf"
    with open(rtf_path, "w", encoding="utf-8") as f:
        f.write(rtf_content)
    print(f"  → {rtf_path}")

    # Open in Pages
    print("用 Pages 打开...")
    subprocess.run(["open", "-a", "Pages", str(rtf_path)])
    print("完成！Pages 打开后可另存为 .pages 格式")


if __name__ == "__main__":
    main()

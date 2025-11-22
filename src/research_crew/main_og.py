#!/usr/bin/env python
# src/research_crew/main.py
import os
from research_crew.crew import ResearchCrew
# We also need these for the bonus index crew
import datetime
from crewai import Agent, Task, Crew

# Create output directory if it doesn't exist
os.makedirs('outputs', exist_ok=True)

# --- 1. Define Topic-Specific Schemas & Briefs ---
# This is the core of the standardization. You define the
# unique instructions for each newsletter page here.

# --- Schema for AI at Work (from your original task) ---
AI_RESEARCH_SCHEMA = """
{
  "topic": "{topic}",
  "selected_product": "<most relevant tool name>",
  "selection_reason": "<100-150 words citing why it was chosen (with facts & dates)>",
  "product_overview": "<80-120 words plain-language description>",
  "core_features": ["...", "...", "..."],
  "whats_new": [
    {"date": "<YYYY-MM>", "change": "<recent update>", "source": "<url>"}
  ],
  "limitations": ["...", "..."],
  "pricing": "<public tiers or 'not publicly disclosed'>",
  "why_it_matters": "<summary linking tool value to manufacturing or enterprise productivity>",
  "sources for tutorial": [
    {"title": "<source title>", "url": "<url>", "date": "<YYYY-MM-DD>", "note": "<why relevant>"}
  ],
  "candidates": [
    {"name": "<tool1>", "note": "<why considered>"},
    {"name": "<tool2>", "note": "<why considered>"}
  ]
}
"""

AI_WRITER_HEADINGS = """
## Main Definition and Key Features: <selected_product>
...
## What's New
...
## Why this Matters
...
## How BEST Group Could Use It
...
## How to use it
...
Sources for tutorials:
- ...
"""

# --- Schema for IT Hacks (NEW) ---
ITHACKS_RESEARCH_SCHEMA = """
{
  "topic": "{topic}",
  "windows_shortcuts": [
    {"shortcut": "<keys>", "Actions": "<what it does>", "Why it's Awesome": "<why these keys are used>"},
    {"shortcut": "<keys>", "Actions": "<what it does>", "Why it's Awesome": "<why these keys are used>"},
    {"shortcut": "<keys>", "Actions": "<what it does>", "Why it's Awesome": "<why these keys are used>"}
  ],
  "mac_shortcuts": [
    {"shortcut": "<keys>", "Actions": "<what it does>", "Why it's Awesome": "<why these keys are used>"},
    {"shortcut": "<keys>", "Actions": "<what it does>", "Why it's Awesome": "<why these keys are used>"},
    {"shortcut": "<keys>", "Actions": "<what it does>", "Why it's Awesome": "<why these keys are used>"}
  ],
  "Quick-Fixes for Everyday": [
    {"issue": "<common problem e.g., 'Slow Wi-Fi'>", "Windows": "<quick fix flow>", "Mac": "<quick fix flow>"},
    {"issue": "<common problem e.g., 'Slow Wi-Fi'>", "Windows": "<quick fix flow>", "Mac": "<quick fix flow>"},
    {"issue": "<common problem e.g., 'Slow Wi-Fi'>", "Windows": "<quick fix flow>", "Mac": "<quick fix flow>"},
    {"issue": "<common problem e.g., 'Slow Wi-Fi'>", "Windows": "<quick fix flow>", "Mac": "<quick fix flow>"},
    {"issue": "<common problem e.g., 'Slow Wi-Fi'>", "Windows": "<quick fix flow>", "Mac": "<quick fix flow>"},
  ],
  "Pro-Tips": [
    {"Tip": "<Pro tips in bullets>"}
  ]
}
"""

ITHACKS_WRITER_HEADINGS = """
Follow these headers 
## Top Time-Saving Windows Shortcuts
...
## Keyboard shortcuts for Mac 
...
## Quick Fixes for Everyday Tech Headaches
...
## Pro-Tips
...
"""

# --- Schema for O365 Updates (NEW) ---
O365_RESEARCH_SCHEMA = """
{
  "topic": "{topic}",
  "selected_product": "<most relevant tool name>",
  "selection_reason": "<100-150 words citing why it was chosen (with facts & dates)>",
  "product_overview": "<80-120 words plain-language description>",
  "core_features": ["...", "...", "..."],
  "Smart Features": [
    {"date": "<YYYY-MM>", "change": "<recent update>", "source": "<url>"}
  ],
  "limitations": ["...", "..."],
  "pricing": "<public tiers or 'not publicly disclosed'>",
  "why_it_matters": "<summary linking tool value to manufacturing or enterprise productivity>",
  "How could it be used in the Manufacturing ecosystem": <summary of the utilization>",
  "sources for tutorial": [
    {"title": "<source title>", "url": "<url>", "date": "<YYYY-MM-DD>", "note": "<why relevant>"}
  ],
  "candidates": [
    {"name": "<tool1>", "note": "<why considered>"},
    {"name": "<tool2>", "note": "<why considered>"}
  ]
}
"""

O365_WRITER_HEADINGS = """
## This Month’s Spotlight: <selected_product>
...
## Why This Matters
...
## How You Can Use <selected_product> at BEST Group
...
## Smart Features You Should Be Using
...
## Pro Tips to Try This Week
...
Sources for tutorials:
- ...
"""
# --- Schema for Tech Discovery (NEW) ---
TECH_DISCOVERY_RESEARCH_SCHEMA = """
{
  "topic": "{topic}",
  "selected_tool": "<name of the innovative tool>",
  "selection_reason": "<100-150 words citing why it was chosen (relevance, innovation, impact)>",
  "tool_overview": "<80-120 words plain-language description>",
  "whats_new": [
    {"date": "<YYYY-MM>", "update": "<recent innovation/milestone>", "source": "<url>"}
  ],
  "why_it_matters": "<summary of the tool's disruptive value>",
  "application_ideas_best_group": ["...", "...", "..."],
  "sources_for_learning": [
    {"title": "<source title>", "url": "<url>"},
    {"title": "<source title>", "url": "<url>"}
  ],
  "candidates": [
    {"name": "<tool1>", "note": "<why considered>"},
    {"name": "<tool2>", "note": "<why considered>"}
  ]
}
"""

TECH_DISCOVERY_WRITER_HEADINGS = """
## Introducing <selected_tool>
...
## What's New
...
## Why This Matters
...
## How BEST Group Could Use It
...
## Where to Learn More
...
"""

# --- Schema for Tech Trends (NEW) ---
TECH_TRENDS_RESEARCH_SCHEMA = """
{
  "topic": "{topic}",
  "selected_trend": "<name of the key trending technology>",
  "selection_reason": "<100-150 words citing why it was chosen (relevance to manufacturing, current hype cycle)>",
  "trend_overview": "<100-150 words plain-language description of the trend>",
  "current_users": [
    {"company": "<Company Name>", "use_case": "<brief application>"},
    {"company": "<Company Name>", "use_case": "<brief application>"}
  ],
  "business_benefits": ["...", "...", "..."],
  "implications_for_best_group": ["...", "...", "..."],
  "sources": [
    {"title": "<source title>", "url": "<url>"},
    {"title": "<source title>", "url": "<url>"}
  ]
}
"""

TECH_TRENDS_WRITER_HEADINGS = """
# <Selected Trend Name>
...
## Introduction
...
## Who is Using It Already?
...
## Benefits Companies are Seeing
...
## What It Means for BEST Group
...
## Sources
...
"""
# --- 2. List of Topics to Generate ---
# This is the "control panel" for your newsletter.
# Add "Tech Discovery" and "Tech Trends" here.

topic_definitions = [
    {
        "topic": "AI at Work",
        "topic_slug": "ai_at_work",
        "research_brief": """
        Topic: "AI at Work"
        Your task is to research AI tools used in an enterprise (shopfloor/corporate) context.

        1.  Identify 3–7 existing or recently launched enterprise-grade AI tools (e.g., Notion AI, Otter.ai, Microsoft Copilot, Grammarly Business) that are currently in active use or officially released — exclude experimental prototypes, superficial utilities, or custom automations that must be built manually.
        2.  From this list, select the **single tool** that you assess as most relevant, impactful, and well-adopted for general enterprise work.
        3.  For this **single selected tool**, conduct a deep-dive research briefing. Find the following information:
            * What the tool is and the specific problem it solves for businesses.
            * Significant new features or improvements from the past 6-12 months (check official blogs, news).
            * Why this tool matters: its key benefits for professionals or organizations.
            * Specific application ideas for "BEST Group," which is a **manufacturing firm**.
            * The tool's pricing model or availability tiers (e.g., Free, Pro, Enterprise).
            * Known limitations, challenges, or integration difficulties to consider.
            * Find 3-5 links to official tutorials or high-quality "getting started" guides or any reference to the articles highlighting its features.

        RULES:
        - All factual claims (features, pricing, release dates) MUST be traceable to verifiable, citable sources (e.g., official product pages, company blogs, or reputable tech media).
        - Use the web_search tool to find all factual information, including features, pricing, and tutorial links.
        """,
        "research_output_schema": f"Your final output MUST be a JSON object matching this exact schema:\n{AI_RESEARCH_SCHEMA}",
        "writer_brief": """
        Using the research output JSON, write a business-friendly article about the **single selected AI tool** for an internal employee newsletter at "BEST Group."

        REQUIREMENTS:
        - The article must be centered on the selected tool.
        - The target audience is professional but not necessarily technical.
        - You MUST follow these five exact headings in this order: (**Note**: Follow the provided Headings only)
            1) Main definition and Key Concepts: **single selected tool**
            2) What's New
            3) Why this Matter
            4) How BEST Group Could Use It
            5) How to use it
        - Under the "How BEST Group Could Use It" section, you MUST describe 3-5 actionable use cases specific to a **manufacturing** environment (e.g., production, quality control, maintenance, supply chain, or corporate functions).
        - Conclude the article with a list of tutorial links, using the exact heading: "Sources for tutorial for [Selected Tool Name]"
        """,
        "writer_output_schema": f"Your final output MUST be a Markdown article matching this structure:\n{AI_WRITER_HEADINGS}",
        "editor_brief": """
        Review and edit the draft article. Your goal is to make it engaging, simple, and easy to understand for all employees, even non-technical ones and Make sure you don't enter any instructions in the article content.

        RULES:
        -   **Tone**: Conversational, helpful, and layman-friendly.
        -   **Introduction**: Add a warm, 2-3 sentence introduction *before* the first heading to engage the reader.
        -   **Clarity**: Simplify any technical jargon or complex AI concepts.
        -   **Headings**: You MUST keep these four exact headings:
            1) Main definition and Key Concepts: [Selected Tool Name]
            2) What's New
            3) Why this Matter
            4) How BEST Group Could Use It
            5) How to use it
        -   **Conclusion**: Ensure the "Sources for tutorial for [Selected Tool Name]" list is present and correctly formatted at the very end.
        -   **Accuracy**: Do not change any factual data (features, names, etc.) from the writer's draft. You MUST limit your output to ≤ 700 words.
        """,
    },
    {
        "topic": "IT Hacks",
        "topic_slug": "it_hacks",
        "research_brief": """
        Topic: "IT Hacks"
        Your task is to find practical, everyday IT tips for employees at BEST Group.

        1.  Find 5-7 genuinely useful **Windows** keyboard shortcuts for common office tasks (e.g., managing windows, text editing, navigating apps).
        2.  Find 5-7 equivalent, useful **macOS** keyboard shortcuts for the same common office tasks.
        3.  Find 5-6 common computer malfunctions (e.g., "Slow Wi-Fi," "Printer not connecting," "Application frozen," "Computer running slow") and their single, most common quick-fix solution.
        4.  Find 3-4 general "pro-tips" for computer efficiency or digital wellness (e.g., the importance of regular restarts, basic password manager advice, clearing browser cache).
        """,
        "research_output_schema": f"Your final output MUST be a JSON object matching this exact schema:\n{ITHACKS_RESEARCH_SCHEMA}",
        "writer_brief": """
        Using the research JSON, create a practical "IT Hacks" article for the internal newsletter. The article must be highly scannable and easy to digest quickly.

        REQUIREMENTS:
        -   Follow these four exact headings in this order:(**Note**: Follow the provided Headings only)
            1) Top Time-Saving Windows Shortcuts
            2) Top Time-Saving for Mac Shortcuts
            3) Quick Fixes for Everyday Tech Headaches
            4) Pro-Tips
        -   Format all shortcuts, troubleshooting steps, and tips as scannable bullet points.
        -   For shortcuts, clearly list the key combination and its action (e.g., `Ctrl + C`: Copy selected item).
        -   For fixes, state the problem and the quick-fix solution.
        """,
        "writer_output_schema": f"Your final output MUST be a Markdown article matching this structure:\n{ITHACKS_WRITER_HEADINGS}",
        "editor_brief": """
        Review and edit the "IT Hacks" draft. Make it extremely clear, simple, and scannable.

        RULES:
        -   **Tone**: Helpful, clear, and direct.
        -   **Introduction**: Add a short, engaging 1-sentence intro *before* the first heading about how these tips save time.
        -   **Headings**: You MUST keep these four exact headings:
            1) Top Time-Saving Windows Shortcuts
            2) Top Time-Saving for Mac Shortcuts
            3) Quick Fixes for Everyday Tech Headaches
            4) Pro-Tips
        -   **Formatting**: Ensure all lists (bullets, etc.) are clean, consistent, and easy to read. You MUST limit your output to ≤ 700 words.
        """,
    },
    {
        "topic": "O365 Updates",
        "topic_slug": "o365_updates",
        "research_brief": """
        Topic: "O365 Updates"
        Your task is to spotlight **one (1)** valuable Microsoft 365 tool that is highly relevant to BEST Group (a manufacturing firm) but may be underutilized by employees.

        1.  Select **one tool** from the M365 suite that fits this description (e.g. Microsoft Lists, Microsoft Planner, Power Automate).
        2.  For this **single selected tool**, find 2-3 powerful reasons why it is valuable for a manufacturing company.
        3.  Research and summarize how this tool could be used by specific corporate departments: HR, Finance, Legal/Compliance, IT, and Sales & Marketing.
        4.  Find 4-5 "Smart Features" of this tool. For each feature, find the information needed to populate this structure:
            * Feature Name
            * What it Does (a brief description)
            * Why You'll Love It (the key benefit)
            * How to Do It (a very brief "how-to," e.g., "Click the 'Share' button")
        5.  Find 3-4 links to official Microsoft tutorials or "getting started" guides for this specific tool.
        """,
        "research_output_schema": f"Your final output MUST be a JSON object matching this exact schema:\n{O365_RESEARCH_SCHEMA}",
        "writer_brief": """
        Using the research JSON, create an article about the **single selected Microsoft 365 tool**. The goal is to drive adoption and show employees the value of tools they already have.

        REQUIREMENTS:
        -   Focus on what's in it for the employee.
        -   You MUST follow these five exact headings in this order:
            1) This Month’s Spotlight: **single selected tool**
            2) Why This Matters
            3) How You Can Use **single selected tool** at BEST Group
            4) Smart Features You Should Be Using
            5) Pro Tips to Try This Week
        -   Under "This Month’s Spotlight: **single selected tool**", introduce the selected tool.
        -   Smart Features You Should Be Using should be in tabular format.
        -   Explain the value in simple, non-technical terms.
        -   End the article with a "Sources" list containing the tutorial links found in the research or any reference to the articles highlighting its features.
        """,
        "writer_output_schema": f"Your final output MUST be a Markdown article matching this structure:\n{O365_WRITER_HEADINGS}",
        "editor_brief": """
        Review and edit the "O365 Updates" draft. The writer's draft uses generic headings; your job is to apply more specific, engaging headings and structure.

        RULES:
        -   **Tone**: Informative, helpful, and enthusiastic.
        -   **Introduction**: Add a 2-sentence intro about getting more value from the Microsoft tools BEST Group already pays for.
        -   **Headings**: You MUST **replace** the writer's headings and use these **five** exact headings:
            1) This Month’s Spotlight: [Selected Tool Name]
            2) Why This Matters
            3) How You Can Use [Selected Tool Name] at BEST Group
            4) Smart Features You Should Be Using
            5) Pro Tips to Try This Week
        -   **Content Mapping**:
            * Use the research content to fill these new sections.
            * The "How You Can Use..." section should contain the department-specific use cases.
            * The "Smart Features..." section should be formatted as a table (if possible) or a clear list based on the research.
            * The Smart Features should have the headers: Feature Name, What it Does (a brief description), Why You'll Love It (the key benefit), How to Do It (a very brief "how-to," e.g., "Click the 'Share' button")
            * "Pro Tips..." can be synthesized from the tool's main benefits or features.
        -   **Clarity**: Simplify any Microsoft-specific jargon.
        -   **Conclusion**: Ensure the "Sources for the tutorial of [Selected Tool Name]" list is present and correct at the very end.
        -   You MUST limit your output to ≤ 700 words.
        """,
    },
    # ... existing topic_definitions list continues ...

    {
        "topic": "Tech Discovery",
        "topic_slug": "tech_discovery",
        "research_brief": """
        Topic: "Tech Discovery"
        Your task is to identify and research one highly innovative and emerging software tool or platform that could significantly change how corporate or shopfloor work is done at a manufacturing firm like BEST Group.

        1.  First, brainstorm 2-3 innovative software options (e.g., a cutting-edge data visualization platform, a new predictive maintenance tool, or a novel digital twin software).
        2.  Select the **single tool** that is the most innovative and has the highest future potential for BEST Group. This is your 'selected_tool'.
        3.  Conduct a research deep-dive on this **single selected tool**:
            * What the tool is and its core innovation.
            * Key recent updates or milestones from the past 6 months.
            * The tool's disruptive value (Why it Matters).
            * 3-5 specific, visionary application ideas for BEST Group in a manufacturing context.
            * Find 3-5 high-quality sources or "where to learn more" links.

        RULES:
        - All information must be verifiable.
        - Focus solely on the future impact and practical application for manufacturing.
        """,
        "research_output_schema": f"Your final output MUST be a JSON object matching this exact schema:\n{TECH_DISCOVERY_RESEARCH_SCHEMA}",
        "writer_brief": """
        Using the research output JSON, write an exciting and forward-looking article about the **selected tool** for the BEST Group newsletter.
        - Don't repeat the tool that is already covered in page.

        REQUIREMENTS:
        - The tone should be engaging and focused on future possibility.
        - You MUST follow these five exact headings in this order:
            1) Introducing [Selected Tool Name]
            2) What's New
            3) Why This Matters
            4) How BEST Group Could Use It
            5) Where to Learn More
        - The "How BEST Group Could Use It" section must feature the visionary application ideas identified in the research.
        - The final section, "Where to Learn More," should list the source links from the research.
        """,
        "writer_output_schema": f"Your final output MUST be a Markdown article matching this structure:\n{TECH_DISCOVERY_WRITER_HEADINGS}",
        "editor_brief": """
        Review and edit the draft article to ensure it is inspirational, clear, and easy for any employee to understand.You MUST limit your output to ≤ 700 words.

        RULES:
        -   **Tone**: Futuristic, professional, and exciting.
        -   **Introduction**: Add a short, intriguing 2-sentence introduction about the future of work at BEST Group.
        -   **Clarity**: Simplify any technical jargon related to the tool's mechanics, focusing on the *benefit*.
        -   **Headings**: You MUST keep these five exact headings:
            1) Introducing [Selected Tool Name]
            2) What's New
            3) Why This Matters
            4) How BEST Group Could Use It
            5) Where to Learn More
        -   **Accuracy**: Preserve all factual claims about the tool's capabilities.
        """
    },
    {
        "topic": "Tech Trends",
        "topic_slug": "tech_trends",
        "research_brief": """
        Topic: "Tech Trends"
        Your task is to identify and analyze one major technology trend currently impacting the manufacturing sector globally.

        1.  First, brainstorm 2-3 high-impact trends (e.g., Hyper-personalization, Generative AI for R&D, IoT-driven Digital Twins, or Advanced Robotics).
        2.  Select the **single most trending and relevant technology** to the manufacturing sector right now. This is your 'selected_trend'.
        3.  Conduct a deep-dive research briefing on this **single selected trend**:
            * An overview defining the trend in simple terms.
            * Identify 3-5 well-known companies (ideally in manufacturing or related industries) that are already using this trend, and briefly describe their use case.
            * Summarize the 3-5 key business benefits companies are seeing (e.g., cost reduction, speed, quality improvement).
            * Provide 3-5 specific implications/action items for BEST Group regarding this trend (What should they watch out for? What's the first step?).
            * Find 3-5 sources to cite.

        RULES:
        - The selected trend must be a broad concept, not a single product.
        - All information must be verifiable and focused on real-world business impact.
        """,
        "research_output_schema": f"Your final output MUST be a JSON object matching this exact schema:\n{TECH_TRENDS_RESEARCH_SCHEMA}",
        "writer_brief": """
        Using the research output JSON, write a professional report-style article about the **selected technology trend**.

        REQUIREMENTS:
        - The tone must be analytical and fact-based.
        - The title must be the name of the selected trend (e.g., # IoT-Driven Digital Twins).
        - You MUST follow these five exact headings in this order:
            1) Introduction
            2) Who is Using It Already?
            3) Benefits Companies are Seeing
            4) What It Means for BEST Group
            5) Sources
        - The "Who is Using It Already?" section should be presented as a clear list or table of companies and use cases.
        - "What It Means for BEST Group" should be highly actionable.
        """,
        "writer_output_schema": f"Your final output MUST be a Markdown article matching this structure:\n{TECH_TRENDS_WRITER_HEADINGS}",
        "editor_brief": """
        Review and edit the draft article. Your goal is to simplify the language without losing the factual rigor of the trend analysis.You MUST limit your output to ≤ 700 words.

        RULES:
        -   **Tone**: Authoritative, yet easy to read.
        -   **Title**: The title must remain the name of the selected trend.
        -   **Introduction**: Add a compelling 3-4 sentence introduction that sets the stage for the importance of this trend.
        -   **Clarity**: Replace industry jargon with accessible terms.
        -   **Headings**: You MUST keep these five exact headings:
            1) Introduction
            2) Who is Using It Already?
            3) Benefits Companies are Seeing
            4) What It Means for BEST Group
            5) Sources
        -   **Accuracy**: Preserve all factual data, company names, and sources.
        """
    }
]
    # {
    #   "topic": "Tech Discovery",
    #   "topic_slug": "tech_discovery",
    #   "research_brief": "...",
    #   ...etc
    # },
    # {
    #   "topic": "Tech Trends",
    #   "topic_slug": "tech_trends",
    #   "research_brief": "...",
    #   ...etc
    # }


def inject_topic_only(schema: str, topic: str) -> str:
    # Protect the {topic} placeholder, then escape all other braces,
    # then restore the topic.
    tmp = schema.replace("{topic}", "___TOPIC___")
    escaped = tmp.replace("{", "{{").replace("}", "}}")
    return escaped.replace("___TOPIC___", topic)

def run():
    """
    Run the research crew for all defined topics.
    """
    print("--- 🚀 STARTING NEWSLETTER CREW RUN ---")
    generated_files = {}

    for definition in topic_definitions:
        topic = definition["topic"]
        topic_slug = definition["topic_slug"]
        print(f"\n--- Processing Topic: {topic} ---")

        today = datetime.date.today()
        month_name = today.strftime("%B")
        full_output_filename = f"{topic_slug}_{month_name}"

        # Assemble the inputs for this specific topic
        inputs = {
            "topic": topic,
            "topic_slug": topic_slug,
            "output_path": full_output_filename,
            # Format the briefs and schemas with the current topic name
            "research_brief": definition["research_brief"].format(topic=topic),
            "research_output_schema": inject_topic_only(definition["research_output_schema"], topic),
            "writer_brief":definition["writer_brief"].format(topic=topic),
            "writer_output_schema": definition["writer_output_schema"],
            "editor_brief": definition["editor_brief"]
        }

        # Create and run the crew
        # This re-initializes the crew for each topic
        result = ResearchCrew().crew().kickoff(inputs=inputs)

        print(f"--- ✅ Finished Topic: {topic} ---")
        # today = datetime.date.today()
        # month_name = today.strftime("%B")
        # output_filename = f"outputs/{topic_slug}_{month_name}.md"
        print(f"Final output file: {full_output_filename}")
        generated_files[topic] = f"{topic_slug}_{month_name}.md" # Store relative path for index

    print("\n--- ✅ ALL CONTENT PAGES GENERATED ---")
    print(generated_files)

    # --- 3. (Bonus) Generate Main Index Page ---
    print("\n--- 🚀 RUNNING CREW FOR: MAIN PAGE ---")
    
    # Create a simple summary of generated files for the index agent
    file_list = "\n".join([f"- **{topic}**: [{file_path}]({file_path})" for topic, file_path in generated_files.items()])

    # Define a new, simple agent for the index page
    index_writer = Agent(
        role="Newsletter Main Page Compiler",
        goal="Create a main index page that links to all generated articles and provides a 1-sentence teaser for each.",
        backstory="You are an editor compiling the final newsletter. You need to create a simple intro and a list of links with engaging, short descriptions.",
        verbose=True
    )

    index_task = Task(
        description=f"""
        Create a main index page for the BEST Group newsletter.
        The newsletter has the following sections:
        {file_list}

        Your task:
        1. Write a 3-4 sentence welcoming introduction to the newsletter.
        2. For each topic, write a 1-sentence teaser description
           that summarizes the content. (e.g., for "IT Hacks", write "Learn new keyboard shortcuts and quick fixes for common computer problems.")
        3. Format the output as a clean Markdown file.

        Example Format:
        # Welcome to the BEST Group Tech Newsletter!
        [Your 3-4 sentence intro here]

        ## In This Issue:

        ### [AI at Work](ai_at_work.md)
        [Your 1-sentence teaser for AI at Work]

        ### [IT Hacks](it_hacks.md)
        [Your 1-sentence teaser for IT Hacks]
        
        ...and so on for all topics.
        """,
        agent=index_writer,
        expected_output="A final `index.md` file with intro, teasers, and relative links.",
        output_file="outputs/index.md"
    )

    index_crew = Crew(
        agents=[index_writer],
        tasks=[index_task],
        verbose=2
    )

    index_result = index_crew.kickoff()

    print(f"--- ✅ MAIN INDEX PAGE GENERATED ---")
    print(f"Final output file: {index_task.output_file}")
    print("\n\n=== FINAL INDEX PAGE ===\n\n")
    print(index_result.raw)

if __name__ == "__main__":
    run()
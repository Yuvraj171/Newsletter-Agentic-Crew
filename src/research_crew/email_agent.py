from crewai import Agent
from research_crew.llm_factory import make_llm


def create_email_agent():
    model = make_llm(
        "EMAIL_MODEL",
        "gemini-flash-lite-latest",
        max_tokens=600,
        temperature=0.2,
        timeout=600,
    )  # lightweight model for email generation
    return Agent(
        role="Email Sender",
        goal="Send finalized newsletters via Gmail after confirmation.",
        backstory=(
            "You are responsible for formatting and sending newsletters to multiple recipients "
            "using Gmail SMTP after user approval."
        ),
        llm=model,
        allow_delegation=False,
        verbose=True
    )

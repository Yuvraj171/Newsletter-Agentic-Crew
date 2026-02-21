# src/research_crew/crew.py
from crewai import Agent, Crew, Process, Task
import os
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List
from research_crew.llm_factory import make_llm


def _env_int(
    name: str,
    default: int,
    *,
    min_value: int = 0,
    max_value: int | None = None,
) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


@CrewBase
class ResearchCrew():
    """Research crew for comprehensive topic analysis and reporting"""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def researcher(self) -> Agent:
        # Faster, streaming researcher to avoid long blocking calls
        researcher_llm = make_llm(
            "RESEARCH_LLM",
            "gemini-2.0-flash",
            max_tokens=700,
            temperature=0.2,
            timeout=900,
        )
        max_retry_limit = _env_int("RESEARCH_AGENT_RETRY_LIMIT", 2, min_value=0, max_value=10)
        max_rpm = _env_int("RESEARCH_AGENT_MAX_RPM", 12, min_value=1, max_value=120)
        max_iter = _env_int("RESEARCH_AGENT_MAX_ITER", 12, min_value=2, max_value=40)

        return Agent(
            config=self.agents_config['researcher'], # type: ignore[index]
            verbose=True,
            tools=[SerperDevTool()],
            cache=True,
            # Disable CrewAI's reasoning-mode for Gemini models, which can
            # produce empty/None responses with the current provider.
            max_retry_limit=max_retry_limit,
            max_rpm=max_rpm,
            max_iter=max_iter,
            llm=researcher_llm,
        )

    @agent
    def writer(self) -> Agent:
        # Use a smaller/faster model + streaming to keep generations quick
        writer_llm = make_llm(
            "WRITER_MODEL",
            "gemini-2.0-flash",
            max_tokens=800,
            temperature=0.2,
            timeout=900,
        )
        max_rpm = _env_int("WRITER_AGENT_MAX_RPM", 10, min_value=1, max_value=120)
        max_iter = _env_int("WRITER_AGENT_MAX_ITER", 8, min_value=2, max_value=40)

        return Agent(
            config=self.agents_config['writer'], # type: ignore[index]
            verbose=True,
            cache=True,
            max_rpm=max_rpm,
            max_iter=max_iter,
            llm=writer_llm,
        )
    
    @agent
    def editor(self) -> Agent:
        # Gemini LLM for larger editor outputs with higher timeout
        editor_llm = make_llm(
            "EDITOR_MODEL",
            "gemini-2.0-flash",
            max_tokens=1200,
            temperature=0.2,
            timeout=1200,
        )
        max_rpm = _env_int("EDITOR_AGENT_MAX_RPM", 8, min_value=1, max_value=120)
        max_iter = _env_int("EDITOR_AGENT_MAX_ITER", 8, min_value=2, max_value=40)

        return Agent(
            config=self.agents_config['editor'],
            verbose=True,
            cache=True,
            max_rpm=max_rpm,
            max_iter=max_iter,
            llm=editor_llm,
            # reasoning= True,
            # max_reasoning_attempts=1,
            # max_retry_limit= 2
        )

    @task
    def research_task(self) -> Task:
        return Task(
            config=self.tasks_config['research_task'] # type: ignore[index]
        )

    @task
    def write_task(self) -> Task:
        return Task(
            config=self.tasks_config['write_task'], # type: ignore[index]
        )
    
    @task
    def edit_task(self) -> Task:
        return Task(
            config=self.tasks_config['edit_task'], # type: ignore[index]
            output_file='outputs/{output_path}.html'
        )
    

    @crew
    def crew(self) -> Crew:
        """Creates the research crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

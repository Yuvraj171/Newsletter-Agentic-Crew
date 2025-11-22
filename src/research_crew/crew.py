# src/research_crew/crew.py
from crewai import Agent, Crew, Process, Task
from crewai.llm import LLM
import os
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List
from research_crew.llm_factory import make_llm

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

        return Agent(
            config=self.agents_config['researcher'], # type: ignore[index]
            verbose=True,
            tools=[SerperDevTool()],
            cache=True,
            # Disable CrewAI's reasoning-mode for Gemini models, which can
            # produce empty/None responses with the current provider.
            max_retry_limit=2,
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

        return Agent(
            config=self.agents_config['writer'], # type: ignore[index]
            verbose=True,
            cache=True,
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

        return Agent(
            config=self.agents_config['editor'],
            verbose=True,
            cache=True,
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

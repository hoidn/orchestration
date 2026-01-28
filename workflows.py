from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    prompt: str


@dataclass(frozen=True)
class Workflow:
    name: str
    steps: list[WorkflowStep]
    cycle_len: int
    review_every_n_cycles: int | None = None
    review_prompt: str | None = None


def get_workflow(name: str, *, review_every_n_cycles: int | None = None) -> Workflow:
    if name == "standard":
        steps = [
            WorkflowStep("supervisor", "supervisor.md"),
            WorkflowStep("main", "main.md"),
        ]
        return Workflow(name="standard", steps=steps, cycle_len=2)
    if name == "review_cadence":
        steps = [
            WorkflowStep("supervisor", "supervisor.md"),
            WorkflowStep("main", "main.md"),
        ]
        return Workflow(
            name="review_cadence",
            steps=steps,
            cycle_len=2,
            review_every_n_cycles=review_every_n_cycles or 0,
            review_prompt="reviewer.md",
        )
    raise ValueError(f"Unknown workflow '{name}'.")


def resolve_step(workflow: Workflow, *, step_index: int) -> WorkflowStep:
    base_index = step_index % workflow.cycle_len
    cycle_index = step_index // workflow.cycle_len
    if workflow.review_every_n_cycles and workflow.review_every_n_cycles > 0:
        if (cycle_index + 1) % workflow.review_every_n_cycles == 0:
            if workflow.review_prompt:
                return WorkflowStep("reviewer", workflow.review_prompt)
    return workflow.steps[base_index]

from scripts.orchestration.workflows import get_workflow, resolve_step


def test_standard_sequence():
    wf = get_workflow("standard", review_every_n_cycles=None)
    assert resolve_step(wf, step_index=0).prompt == "supervisor.md"
    assert resolve_step(wf, step_index=1).prompt == "main.md"
    assert resolve_step(wf, step_index=2).prompt == "supervisor.md"


def test_standard2_sequence():
    wf = get_workflow("standard2", review_every_n_cycles=None)
    assert resolve_step(wf, step_index=0).prompt == "supervisor2.md"
    assert resolve_step(wf, step_index=1).prompt == "main2.md"
    assert resolve_step(wf, step_index=2).prompt == "supervisor2.md"


def test_review_cadence_replaces_both_steps():
    wf = get_workflow("review_cadence", review_every_n_cycles=2)
    # cycle_len=2, cycle_index=1 should be reviewer for both steps
    assert resolve_step(wf, step_index=2).prompt == "reviewer.md"
    assert resolve_step(wf, step_index=3).prompt == "reviewer.md"
    # next cycle returns to base prompts
    assert resolve_step(wf, step_index=4).prompt == "supervisor.md"


def test_review_cadence2_replaces_both_steps():
    wf = get_workflow("review_cadence2", review_every_n_cycles=2)
    # cycle_len=2, cycle_index=1 should be reviewer for both steps
    assert resolve_step(wf, step_index=2).prompt == "reviewer.md"
    assert resolve_step(wf, step_index=3).prompt == "reviewer.md"
    # next cycle returns to base prompts
    assert resolve_step(wf, step_index=4).prompt == "supervisor2.md"

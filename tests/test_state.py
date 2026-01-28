from scripts.orchestration.state import OrchestrationState


def test_state_defaults_use_workflow_and_step_index():
    st = OrchestrationState()
    assert st.workflow_name == "standard"
    assert st.step_index == 0
    assert st.iteration == 1

from devops_agent_platform.infrastructure.identifiers import UUIDIdentifierGenerator


def test_uuid_identifier_generator_uses_resource_prefixes() -> None:
    generator = UUIDIdentifierGenerator()

    alert_id = generator.new_alert_id()
    incident_id = generator.new_incident_id()
    event_id = generator.new_event_id()
    workflow_run_id = generator.new_workflow_run_id()

    assert alert_id.startswith("alt_")
    assert incident_id.startswith("inc_")
    assert event_id.startswith("evt_")
    assert workflow_run_id.startswith("wfr_")
    assert len(alert_id) == 36
    assert len(incident_id) == 36
    assert len(event_id) == 36
    assert len(workflow_run_id) == 36
    assert generator.new_alert_id() != alert_id

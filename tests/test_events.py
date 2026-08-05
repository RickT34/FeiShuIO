from message_io.events import parse_bind_command


def test_parse_bind_command_accepts_simple_alias():
    assert parse_bind_command("/bind test-1") == "test-1"


def test_parse_bind_command_rejects_invalid_alias():
    assert parse_bind_command("/bind 测试") is None
    assert parse_bind_command("/bind") is None


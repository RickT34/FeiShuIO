from message_io.events import UserCommand, parse_user_command


def test_bind_command_accepts_simple_alias():
    assert parse_user_command("/bind test-1") == UserCommand("bind", "test-1")


def test_parse_binding_management_commands():
    assert parse_user_command("/help") == UserCommand("help")
    assert parse_user_command("/bind") == UserCommand("current")
    assert parse_user_command("/binds") == UserCommand("list")
    assert parse_user_command("/unbind") == UserCommand("unbind")


def test_invalid_known_command_is_not_treated_as_a_regular_message():
    assert parse_user_command("/bind 测试") == UserCommand("invalid")
    assert parse_user_command("/unbind ops") == UserCommand("invalid")
    assert parse_user_command("/unknown") is None

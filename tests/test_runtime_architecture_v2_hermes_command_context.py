from src.runtime_architecture_v2.hermes_command_context import (
    HermesCommandContext,
    read_hermes_command_context,
)


def test_reads_discord_thread_context_from_hermes_session_vars():
    values = {
        "HERMES_SESSION_PLATFORM": "discord",
        "HERMES_SESSION_CHAT_ID": "200",
        "HERMES_SESSION_CHAT_NAME": "Entertainment / #idea-thread",
        "HERMES_SESSION_THREAD_ID": "200",
        "HERMES_SESSION_USER_ID": "300",
        "HERMES_SESSION_USER_NAME": "KBM",
        "HERMES_SESSION_ID": "session-1",
        "HERMES_SESSION_MESSAGE_ID": "250",
        "HERMES_SESSION_PROFILE": "aicompanyassistant",
    }
    context = read_hermes_command_context(
        lambda key, default="": values.get(key, default)
    )
    assert context == HermesCommandContext(
        platform="discord",
        chat_id="200",
        chat_name="Entertainment / #idea-thread",
        thread_id="200",
        user_id="300",
        user_name="KBM",
        session_id="session-1",
        invocation_message_id="250",
        profile="aicompanyassistant",
    )
    assert context.is_discord_thread is True


def test_guild_channel_without_thread_is_not_a_save_boundary():
    context = HermesCommandContext(platform="discord", chat_id="100")
    assert context.is_discord_thread is False


def test_reader_does_not_consume_unsupported_dm_session_start_variable():
    requested = []

    def get_env(key, default=""):
        requested.append(key)
        if key == "HERMES_SESSION_START_MESSAGE_ID":
            return "800"
        return default

    context = read_hermes_command_context(get_env)

    assert context.session_start_message_id == ""
    assert "HERMES_SESSION_START_MESSAGE_ID" not in requested

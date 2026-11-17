import asyncio
import re

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from . import events

YES = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok", "approve", "approved", "allow", "go ahead", "do it", "proceed"}
NO = {"no", "not", "nope", "nah", "stop", "cancel", "deny", "don't", "do not", "never"}
TIMEOUT = 30
MAX_READ = 200


def verdict(text):
    heard = re.sub(r"[^a-z' ]", " ", text.lower())
    words = set(heard.split())
    if words & NO or any(p in heard for p in ("do not", "go ahead and not")):
        return False
    if words & YES or "go ahead" in heard or "do it" in heard:
        return True
    return None


def describe(tool_name, tool_input):
    if tool_name == "Bash":
        what = f"run the command {tool_input.get('command', '')}"
    elif tool_input.get("file_path"):
        what = f"use {tool_name} on {tool_input['file_path']}"
    else:
        what = f"use {tool_name}"
    return what if len(what) <= MAX_READ else what[:MAX_READ] + ", and more"


class Approver:
    def __init__(self, say, listen, timeout=TIMEOUT):
        self.say, self.listen, self.timeout = say, listen, timeout

    async def __call__(self, tool_name, tool_input, context):
        what = describe(tool_name, tool_input)
        events.emit("waiting_for_approval", what)
        question = f"Claude wants to {what}. Allow it?"
        for attempt in range(2):
            self.say(question if attempt == 0 else "Say yes or no.")
            try:
                heard = await asyncio.wait_for(self.listen(), self.timeout)
            except asyncio.TimeoutError:
                break
            answer = verdict(heard or "")
            if answer is not None:
                events.emit("approval", "allowed" if answer else "denied")
                return PermissionResultAllow() if answer else PermissionResultDeny(message="The user said no.")
        events.emit("approval", "denied (no answer)")
        return PermissionResultDeny(message="The user did not answer, so it was denied.")

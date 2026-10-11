"""Strict wire schemas shared by both model providers and the tool dispatcher."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class FindPatient(Arguments):
    name: str = Field(min_length=1)
    dob: str = Field(min_length=1)

class Availability(Arguments):
    clinician: str
    date: str

class Booking(Arguments):
    patient_id: str
    clinician: str
    slot: str

class Cancellation(Arguments):
    appointment_id: str

class Escalation(Arguments):
    patient_id: str
    reason: str = Field(min_length=1)
    urgency: Literal["now", "soon"]

class Message(Arguments):
    patient_id: str
    text: str = Field(min_length=1)

TOOLS = {
    "find_patient": FindPatient,
    "get_availability": Availability,
    "book_appointment": Booking,
    "cancel_appointment": Cancellation,
    "escalate_to_nurse": Escalation,
    "send_message": Message,
}

class Action(Arguments):
    tool: Literal["find_patient", "get_availability", "book_appointment", "cancel_appointment", "escalate_to_nurse", "send_message", "finish"]
    arguments: dict


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_action(text: str) -> Action:
    action = Action.model_validate(json.loads(text, object_pairs_hook=unique_object))
    if action.tool == "finish":
        if set(action.arguments) != {"text"} or type(action.arguments["text"]) is not str:
            raise ValueError("finish requires only a string text")
    else:
        TOOLS[action.tool].model_validate(action.arguments)
    return action


def tool_contract() -> str:
    return json.dumps({name: cls.model_json_schema() for name, cls in TOOLS.items()}, separators=(",", ":"))

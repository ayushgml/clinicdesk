"""A small action-planning loop; the model chooses the clinic workflow."""
import json
from .schema import parse_action, unique_object
from .safety import red_flags

SYSTEM = """You are a clinic front-desk scheduler using MOCK records. Never give medical advice, diagnose symptoms, name suspected conditions, or recommend treatments. Only describe administrative routing.
Verify the caller's own name AND date of birth via find_patient before revealing or changing records. Never disclose another patient's information, even to family. When a request mixes permitted scheduling and private information, complete the permitted scheduling and decline only the private information. Missing DOB: ask for it; never guess. Multiple names can match. Name alone is insufficient.
Chest pain, breathing difficulty, stroke signs, severe bleeding or indirect equivalents require escalate_to_nurse urgency now; do not book routine care. Fact red_flags is authoritative when nonempty. Nurse routing can use patient_id unverified if identity is missing. Respect clinician specialties. Never double-book a patient or clinician. At most one new booking per entire request. Cancel before rescheduling. Confirm each successful action with send_message to the verified patient. Do not claim success for a blocked/failed action.
Return ONLY a JSON array of actions, each {"tool":NAME,"arguments":OBJECT}. You may plan multiple ordered actions per reply. Use $patient for the verified patient's ID and $appointment for the last created appointment ID. Observations arrive after execution. Finish only after the requested operation and confirmation, or when asking for missing information/refusing unauthorized access.
Example syntax: [{"tool":"find_patient","arguments":{"name":"Alex Morgan","dob":"1985-04-12"}}]. Arguments MUST be a JSON object with named keys, never an array.
Tools (all arguments required, strings):
find_patient(name,dob); get_availability(clinician,date); book_appointment(patient_id,clinician,slot); cancel_appointment(appointment_id); escalate_to_nurse(patient_id,reason,urgency=now|soon); send_message(patient_id,text); finish(text).
Clinicians: dr_lee general; dr_rao dermatology; dr_kim cardiology. Use ISO slots, e.g. 2026-11-02T09:00. Availability must be checked before booking. Do not put private appointment annotations in confirmations. Be concise. No markdown fences. Always use $patient after find_patient; never invent an ID. Plan related actions together, including send_message and finish. After a confirmation succeeds, finish; do not repeat the workflow."""

class Agent:
    history_turns = 12
    max_rounds = 8

    def __init__(self, llm, tools):
        self.llm, self.tools = llm, tools
        self.turns = []
        self.responses = []
        self.model_log = []
        self.last_appointment = None
        self.pending_confirmation = False

    def _confirm_pending(self):
        if self.pending_confirmation and self.tools.verified:
            updates = []
            for entry in self.tools.log:
                if entry["outcome"] != "ok":
                    continue
                if entry["tool"] in ("book_appointment", "cancel_appointment"):
                    row = entry["result"]["appointment"]
                    updates.append(f"Appointment {row['id']}: {row['status']} for {row['slot']}")
                elif entry["tool"] == "escalate_to_nurse":
                    updates.append("Nurse review requested")
            self.tools.call("send_message", {"patient_id": self.tools.verified, "text": "; ".join(updates) or "Clinic request processed."})
            self.pending_confirmation = False

    def _resolve(self, arguments):
        values = {}
        for key, value in arguments.items():
            if value == "$patient":
                value = self.tools.verified or "unverified"
            if value == "$appointment":
                value = self.last_appointment or "unknown"
            values[key] = value
        return values

    def turn(self, message: str):
        self.turns.append({"message": message, "exchanges": []})
        self.turns = self.turns[-self.history_turns:]
        request = "\n".join(t["message"] for t in self.turns)
        self.tools.set_request(request)
        self.tools.verify_supplied_identity()
        detected = red_flags(request)
        routed = False
        if detected and self.tools.verified:
            self.tools.call("escalate_to_nurse", {"patient_id": self.tools.verified, "reason": "; ".join(detected), "urgency": "now"})
            self.tools.call("send_message", {"patient_id": self.tools.verified, "text": "Your request has been routed to the nurse for immediate review."})
            routed = True
        messages = []
        for turn in self.turns:
            messages.append({"role": "user", "content": turn["message"]})
            messages.extend(turn["exchanges"])
        facts = {"red_flags": red_flags(request), "verified_patient": self.tools.verified, "nurse_notified_and_confirmed": routed}
        messages[-1]["content"] += "\nRouting facts: " + json.dumps(facts)
        for _ in range(self.max_rounds):
            reply = self.llm.complete(SYSTEM, messages)
            self.model_log.append({"reply": reply.text, "facts": facts})
            exchanges = [{"role": "assistant", "content": reply.text}]
            try:
                raw = json.loads(reply.text, object_pairs_hook=unique_object)
                if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
                    raise ValueError("Return 1–8 actions as a JSON array")
                actions = [parse_action(json.dumps(a)) for a in raw]
            except (ValueError, TypeError) as exc:
                exchanges.append({"role": "user", "content": f"Invalid action format: {exc}. Correct it."})
                messages.extend(exchanges)
                self.turns[-1]["exchanges"].extend(exchanges)
                continue
            observations = []
            finished = False
            confirmed = False
            for action in actions:
                if action.tool == "finish":
                    self._confirm_pending()
                    self.responses.append(action.arguments["text"])
                    finished = True
                    break
                args = self._resolve(action.arguments)
                result = self.tools.call(action.tool, args)
                observations.append({"tool": action.tool, "result": result})
                if result["status"] == "ok":
                    if action.tool in ("book_appointment", "cancel_appointment", "escalate_to_nurse"):
                        self.pending_confirmation = True
                    elif action.tool == "send_message":
                        self.pending_confirmation = False
                        confirmed = True
                if action.tool == "book_appointment" and result["status"] == "ok":
                    self.last_appointment = result["appointment"]["id"]
                if action.tool == "get_availability":
                    break
                if result["status"] == "blocked":
                    break
            exchanges.append({"role": "user", "content": "Tool observations: " + json.dumps(observations, separators=(",", ":"))})
            messages.extend(exchanges)
            self.turns[-1]["exchanges"].extend(exchanges)
            if finished or (confirmed and not self.pending_confirmation):
                return
        self._confirm_pending()
        self.responses.append("I could not complete this request. Please contact the clinic front desk.")

"""Regenerates the two n8n workflow JSON files. Run: python n8n/build_workflows.py"""
import json
import uuid
from pathlib import Path

AGENT = "http://agent:8000"          # inside docker-compose; use http://host.docker.internal:8000 for local tests
KEY = "REPLACE_WITH_MATRIX_API_KEY"
OWNER = "REPLACE_WITH_OWNER_TELEGRAM_ID"
HERE = Path(__file__).parent


def node(name, type_, version, params, pos, creds=None):
    n = {"parameters": params, "id": str(uuid.uuid4()), "name": name, "type": type_,
         "typeVersion": version, "position": pos}
    if creds:
        n["credentials"] = creds
    return n


def if_node(name, left, op, right=None, pos=(0, 0)):
    cond = {"id": str(uuid.uuid4()), "leftValue": left, "operator": op}
    if right is not None:
        cond["rightValue"] = right
    return node(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                       "conditions": [cond], "combinator": "and"},
        "options": {}}, list(pos))


def http(name, path, body_expr, pos):
    return node(name, "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": f"{AGENT}{path}",
        "sendHeaders": True, "headerParameters": {"parameters": [{"name": "X-Matrix-Key", "value": KEY}]},
        "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify(" + body_expr + ") }}",
        "options": {"timeout": 120000}}, list(pos))


TG_CREDS = {"telegramApi": {"id": "REPLACE", "name": "Cross Coat Telegram Bot"}}
SLACK_CREDS = {"slackApi": {"id": "REPLACE", "name": "Cross Coat Slack"}}


def tg_send(name, chat_expr, text_expr, pos, buttons=False):
    p = {"chatId": chat_expr, "text": text_expr, "additionalFields": {"appendAttribution": False}}
    if buttons:
        p["replyMarkup"] = "inlineKeyboard"
        p["inlineKeyboard"] = {"rows": [{"row": {"buttons": [
            {"text": "YES - approve", "additionalFields": {"callback_data": "={{ 'yes|' + $json.thread_id }}"}},
            {"text": "NO - cancel", "additionalFields": {"callback_data": "={{ 'no|' + $json.thread_id }}"}},
        ]}}]}
    return node(name, "n8n-nodes-base.telegram", 1.2, p, list(pos), TG_CREDS)


def conn(*pairs):
    """pairs: (from, [[targets of output 0], [targets of output 1]])"""
    return {src: {"main": [[{"node": t, "type": "main", "index": 0} for t in outs] for outs in outputs]}
            for src, outputs in pairs}


ASK_TEXT = "={{ '🔔 ' + $json.question + '\\n\\nDraft:\\n' + $json.draft }}"
BOOL_TRUE = {"type": "boolean", "operation": "true", "singleValue": True}
STR_EQ = {"type": "string", "operation": "equals"}

# ---------------- Workflow 1: Telegram inbound + approval buttons ----------------
# One Telegram bot = one webhook, so messages AND button presses live in the same workflow.
tg = [
    node("Telegram Trigger", "n8n-nodes-base.telegramTrigger", 1.2,
         {"updates": ["message", "callback_query"], "additionalFields": {}}, [0, 300], TG_CREDS),
    if_node("Owner Only?", "={{ String(($json.message || $json.callback_query).from.id) }}", STR_EQ, OWNER, (220, 300)),
    if_node("Button Press?", "={{ !!$json.callback_query }}", BOOL_TRUE, pos=(440, 300)),
    http("Call /approve", "/approve",
         "{ thread_id: $json.callback_query.data.split('|')[1], decision: $json.callback_query.data.split('|')[0], "
         "approver_id: String($json.callback_query.from.id) }", (660, 160)),
    node("Answer Button", "n8n-nodes-base.telegram", 1.2,
         {"resource": "callback", "queryId": "={{ $('Telegram Trigger').item.json.callback_query.id }}",
          "additionalFields": {"text": "Got it"}}, [880, 160], TG_CREDS),
    tg_send("Send Approval Result", "={{ $('Telegram Trigger').item.json.callback_query.message.chat.id }}",
            "={{ $('Call /approve').item.json.reply }}", (1100, 160)),
    http("Call /inbound", "/inbound",
         "{ text: $json.message.text || '', source: 'telegram', chat_id: String($json.message.chat.id), "
         "user_id: String($json.message.from.id) }", (660, 440)),
    if_node("Needs Approval?", "={{ $json.status }}", STR_EQ, "needs_approval", (880, 440)),
    tg_send("Ask Owner (Yes/No)", "={{ $('Telegram Trigger').item.json.message.chat.id }}", ASK_TEXT, (1100, 360), buttons=True),
    tg_send("Send Reply", "={{ $('Telegram Trigger').item.json.message.chat.id }}", "={{ $json.reply }}", (1100, 520)),
]
tg_wf = {"name": "Cross Coat Matrix - Telegram (inbound + approvals)", "nodes": tg,
         "connections": conn(("Telegram Trigger", [["Owner Only?"]]),
                             ("Owner Only?", [["Button Press?"], []]),
                             ("Button Press?", [["Call /approve"], ["Call /inbound"]]),
                             ("Call /approve", [["Answer Button"]]),
                             ("Answer Button", [["Send Approval Result"]]),
                             ("Call /inbound", [["Needs Approval?"]]),
                             ("Needs Approval?", [["Ask Owner (Yes/No)"], ["Send Reply"]])),
         "settings": {"executionOrder": "v1", "timezone": "America/Edmonton"}, "active": False, "pinData": {}}

# ---------------- Workflow 2: Slack inbound (approvals still go to the owner on Telegram) ----------------
slack_reply = lambda name, text, pos: node(name, "n8n-nodes-base.slack", 2.3, {
    "select": "channel",
    "channelId": {"__rl": True, "value": "={{ $('Slack Trigger').item.json.channel }}", "mode": "id"},
    "text": text, "otherOptions": {"includeLinkToWorkflow": False}}, list(pos), SLACK_CREDS)

sl = [
    node("Slack Trigger", "n8n-nodes-base.slackTrigger", 1,
         {"trigger": ["message"], "watchWorkspace": True, "options": {}}, [0, 300], SLACK_CREDS),
    if_node("Not A Bot?", "={{ !$json.bot_id }}", BOOL_TRUE, pos=(220, 300)),
    http("Call /inbound", "/inbound",
         "{ text: $json.text || '', source: 'slack', chat_id: $json.channel, user_id: $json.user }", (440, 300)),
    if_node("Needs Approval?", "={{ $json.status }}", STR_EQ, "needs_approval", (660, 300)),
    tg_send("Ask Owner on Telegram", OWNER, ASK_TEXT, (880, 200), buttons=True),
    slack_reply("Slack: Waiting", "Waiting for the owner's approval on Telegram.", (1100, 200)),
    slack_reply("Slack: Reply", "={{ $('Call /inbound').item.json.reply }}", (880, 400)),
]
sl_wf = {"name": "Cross Coat Matrix - Slack inbound", "nodes": sl,
         "connections": conn(("Slack Trigger", [["Not A Bot?"]]),
                             ("Not A Bot?", [["Call /inbound"], []]),
                             ("Call /inbound", [["Needs Approval?"]]),
                             ("Needs Approval?", [["Ask Owner on Telegram"], ["Slack: Reply"]]),
                             ("Ask Owner on Telegram", [["Slack: Waiting"]])),
         "settings": {"executionOrder": "v1", "timezone": "America/Edmonton"}, "active": False, "pinData": {}}

for fname, wf in [("telegram_inbound_and_approval.json", tg_wf), ("slack_inbound.json", sl_wf)]:
    (HERE / fname).write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", fname)

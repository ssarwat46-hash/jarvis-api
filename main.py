from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, json, httpx, psycopg2, secrets
from datetime import datetime

app = FastAPI(title="Jarvis API", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# API Keys with permission levels
API_KEYS = {
      os.getenv("API_KEY_TELEGRAM", "telegram-key-change-me"): 1,
      os.getenv("API_KEY_CLI", "cli-key-change-me"): 2,
      os.getenv("API_KEY_DEVICE", "device-key-change-me"): 3,
      os.getenv("API_KEY_ADMIN", "admin-key-change-me"): 4,
}

DATABASE_URL = os.getenv("DATABASE_URL")
N8N_BASE = os.getenv("N8N_WEBHOOK_BASE_URL", "https://n8n-acpt.onrender.com")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

INTENT_TO_WEBHOOK = {
      "log_finance": "/webhook/jarvis/log-finance",
      "log_workout": "/webhook/jarvis/log-workout",
      "log_meal": "/webhook/jarvis/log-meal",
      "log_habit": "/webhook/jarvis/log-habit",
      "analyze_ghl": "/webhook/jarvis/analyze-ghl",
      "wake_pc": "/webhook/jarvis/wake-pc",
      "work_mode": "/webhook/jarvis/work-mode",
      "restart_server": "/webhook/jarvis/restart-server",
      "monitor_leads": "/webhook/jarvis/monitor-leads",
      "summarize_conversation": "/webhook/jarvis/summarize-conversation",
      "show_report": "/webhook/jarvis/show-report",
      "run_command": "/webhook/jarvis/run-command",
      "open_app": "/webhook/jarvis/open-app",
      "shutdown_pc": "/webhook/jarvis/shutdown-pc",
      "export_sms": "/webhook/jarvis/export-sms",
}

SYSTEM_PROMPT = """You are Jarvis, a personal AI operations system for Sadnan.
Parse the user command into structured JSON intent.
Return ONLY valid JSON with:
- intent: string (log_finance|log_workout|log_meal|log_habit|analyze_ghl|wake_pc|work_mode|restart_server|monitor_leads|summarize_conversation|show_report|run_command|open_app|shutdown_pc|export_sms|chat)
- params: object with relevant parameters extracted from the command
- required_permission: int (1=read, 2=automation, 3=device_control, 4=system)
- description: plain English summary of what will happen
- reply: friendly conversational reply to send back to user

Examples:
"jarvis log expense 500 bdt groceries" -> {"intent":"log_finance","params":{"type":"expense","amount":500,"currency":"BDT","category":"groceries","description":"groceries"},"required_permission":2,"description":"Logging BDT 500 grocery expense","reply":"Got it! Logged BDT 500 grocery expense."}
"jarvis wake PC" -> {"intent":"wake_pc","params":{},"required_permission":3,"description":"Sending Wake-on-LAN to PC","reply":"Waking your PC now..."}
"jarvis log workout shoulder press 3 sets 12 reps 20kg" -> {"intent":"log_workout","params":{"exercise":"shoulder press","sets":3,"reps":12,"weight_kg":20},"required_permission":2,"description":"Logging shoulder press workout","reply":"Workout logged! Shoulder press 3x12 @ 20kg"}
"jarvis start work mode" -> {"intent":"work_mode","params":{"apps":["chrome","ghl","notion","n8n"]},"required_permission":3,"description":"Starting work mode on PC","reply":"Starting work mode - opening your apps!"}
"jarvis show today revenue" -> {"intent":"show_report","params":{"report_type":"daily_finance","period":"today"},"required_permission":1,"description":"Fetching today finance report","reply":"Fetching your revenue report..."}
"jarvis analyze GHL conversations last 24 hours" -> {"intent":"analyze_ghl","params":{"timeframe":"24h"},"required_permission":2,"description":"Analyzing GHL conversations","reply":"Analyzing GHL conversations from the last 24 hours..."}
"""

def get_db():
      return psycopg2.connect(DATABASE_URL)

def verify_api_key(x_api_key: str = Header(...)):
      level = API_KEYS.get(x_api_key)
      if not level:
                raise HTTPException(status_code=403, detail="Invalid API key")
            return level

class CommandRequest(BaseModel):
      command: str
    source: str = "unknown"
    context: dict = {}

class FinanceEntry(BaseModel):
      type: str
    amount: float
    currency: str = "USD"
    category: str = ""
    description: str = ""

class WorkoutEntry(BaseModel):
      exercise: str
    sets: int
    reps: int
    weight_kg: float = 0
    notes: str = ""

class HabitEntry(BaseModel):
      sleep: bool = False
    gym: bool = False
    sales_work: bool = False
    meditation: bool = False
    journaling: bool = False
    reading: bool = False

async def interpret_command(command: str, context: dict = {}) -> dict:
      async with httpx.AsyncClient() as client:
                resp = await client.post(
                              "https://api.openai.com/v1/chat/completions",
                              headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                              json={
                                                "model": "gpt-4o",
                                                "messages": [
                                                                      {"role": "system", "content": SYSTEM_PROMPT},
                                                                      {"role": "user", "content": command}
                                                ],
                                                "response_format": {"type": "json_object"}
                              },
                              timeout=30
                )
            return json.loads(resp.json()["choices"][0]["message"]["content"])

async def dispatch_to_n8n(intent: dict, cmd_id: int) -> dict:
      webhook_path = INTENT_TO_WEBHOOK.get(intent.get("intent"))
    if not webhook_path:
              return {"status": "no_handler", "message": f"No workflow for: {intent.get('intent')}"}
          try:
                    async with httpx.AsyncClient(timeout=30) as client:
                                  resp = await client.post(
                                                    f"{N8N_BASE}{webhook_path}",
                                                    json={"intent": intent, "cmd_id": cmd_id, "params": intent.get("params", {})}
                                  )
                                  return resp.json() if resp.text else {"status": "dispatched"}
          except Exception as e:
        return {"status": "dispatched", "note": str(e)}

def log_command_db(source, raw_text, intent):
      try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO commands (source, raw_text, intent, payload, status) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (source, raw_text, intent.get("intent"), json.dumps(intent), "pending")
                )
                cmd_id = cur.fetchone()[0]
                conn.commit()
                cur.close()
                conn.close()
                return cmd_id
            except:
        return 0

              @app.get("/health")
def health():
      return {"status": "alive", "service": "jarvis-api", "timestamp": datetime.utcnow().isoformat()}

@app.post("/command")
async def handle_command(req: CommandRequest, permission: int = Depends(verify_api_key)):
      intent = await interpret_command(req.command, req.context)
    required = intent.get("required_permission", 1)
    if permission < required:
              raise HTTPException(status_code=403, detail=f"Command requires Level {required} access. You have Level {permission}.")
          cmd_id = log_command_db(req.source, req.command, intent)
    result = await dispatch_to_n8n(intent, cmd_id)
    return {
              "status": "ok",
              "intent": intent.get("intent"),
              "description": intent.get("description"),
              "reply": intent.get("reply", intent.get("description")),
              "params": intent.get("params", {}),
              "result": result,
              "cmd_id": cmd_id
    }

@app.get("/commands/pending")
def get_pending_commands(device: str = "pc", permission: int = Depends(verify_api_key)):
            try:
                      conn = get_db()
                      cur = conn.cursor()
                      cur.execute("SELECT id, intent, payload FROM commands WHERE status='pending' AND (payload->>'device'=%s OR payload->>'device' IS NULL) LIMIT 10", (device,))
                      rows = cur.fetchall()
                      cur.close()
                      conn.close()
                      return [{"id": r[0], "intent": {"intent": r[1], **json.loads(r[2])}} for r in rows]
                  except:
        return []

@app.post("/commands/{cmd_id}/complete")
def complete_command(cmd_id: int, body: dict = {}, permission: int = Depends(verify_api_key)):
      try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("UPDATE commands SET status='completed', result=%s WHERE id=%s", (str(body.get("result","")), cmd_id))
                conn.commit()
                cur.close()
                conn.close()
            except:
        pass
    return {"status": "ok"}

@app.post("/log/finance")
def log_finance(entry: FinanceEntry, permission: int = Depends(verify_api_key)):
      conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO finances (type, amount, currency, category, description, source) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                                (entry.type, entry.amount, entry.currency, entry.category, entry.description, "api"))
    row_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "id": row_id}

@app.post("/log/workout")
def log_workout(entry: WorkoutEntry, permission: int = Depends(verify_api_key)):
      conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO workouts (exercise, sets, reps, weight_kg, notes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                (entry.exercise, entry.sets, entry.reps, entry.weight_kg, entry.notes))
    row_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "id": row_id}

@app.post("/log/habit")
def log_habit(entry: HabitEntry, permission: int = Depends(verify_api_key)):
      xp = (20 if entry.sleep else 0) + (25 if entry.gym else 0) + (20 if entry.sales_work else 0) + \
         (15 if entry.meditation else 0) + (10 if entry.journaling else 0) + (10 if entry.reading else 0)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO habits (sleep, gym, sales_work, meditation, journaling, reading, xp_earned)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                                          ON CONFLICT (habit_date) DO UPDATE SET
                                                             sleep=EXCLUDED.sleep, gym=EXCLUDED.gym, sales_work=EXCLUDED.sales_work,
                                                                                meditation=EXCLUDED.meditation, journaling=EXCLUDED.journaling,
                                                                                                   reading=EXCLUDED.reading, xp_earned=EXCLUDED.xp_earned
                                                                                                                      RETURNING id""",
                                (entry.sleep, entry.gym, entry.sales_work, entry.meditation, entry.journaling, entry.reading, xp))
    row_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "id": row_id, "xp_earned": xp}

@app.get("/report/finance/today")
def finance_today(permission: int = Depends(verify_api_key)):
      conn = get_db()
    cur = conn.cursor()
    cur.execute("""SELECT type, currency, SUM(amount) as total FROM finances
                       WHERE DATE(logged_at) = CURRENT_DATE GROUP BY type, currency""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"date": str(datetime.utcnow().date()), "summary": [{"type": r[0], "currency": r[1], "total": float(r[2])} for r in rows]}

@app.get("/report/habits/today")
def habits_today(permission: int = Depends(verify_api_key)):
      conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM habits WHERE habit_date = CURRENT_DATE")
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
              return {"date": str(datetime.utcnow().date()), "message": "No habits logged today yet"}
          return {"date": str(row[1]), "sleep": row[2], "gym": row[3], "sales_work": row[4],
                              "meditation": row[5], "journaling": row[6], "reading": row[7], "xp_earned": row[8]}

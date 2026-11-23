"""No-network tour of the actual router policy and persistent memory."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import tempfile
from router.core import Policy, Request, decide
from agent_memory.store import Store

for text in ["What is 2 + 2?", "Prove that 2 is even.", "[private] Summarize synthetic notes."]:
    d=decide(Request(text),Policy.load())
    print(json.dumps({"route":d.route,"reason":d.reason,"inference_executed":False}))
with tempfile.TemporaryDirectory() as folder:
    # Explicit fictional preference; no production stores are opened.
    store=Store(folder,{101})
    store.write(101,'communication_style','concise')
    restored=Store(folder,{101})
    print(json.dumps({"preference_recalled_from_new_store":restored.read(101)['facts']['communication_style']['value']=='concise',"network_used":False}))

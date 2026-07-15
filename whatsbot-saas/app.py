"""
WhatsBot SaaS — Servidor Central
Integra Twilio (WhatsApp) + Gemini (IA)
Roda no Render.com gratuitamente
"""

import os
import json
import re
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from collections import Counter

# ── Dados em memória (Render usa disco efêmero) ───────────────────────────
# Em produção futura, trocar por banco de dados
CLIENTES = {}   # { "id": { config, padroes, correcoes, historico } }
LOGS = []

def log(msg):
    entrada = {"hora": datetime.now().strftime("%H:%M:%S %d/%m"), "msg": str(msg)}
    LOGS.insert(0, entrada)
    if len(LOGS) > 500:
        LOGS.pop()
    print("[LOG]", entrada["hora"], "-", msg)

# ── Persistência simples em arquivo JSON ──────────────────────────────────
DATA_FILE = "clientes.json"

def salvar():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(CLIENTES, f, ensure_ascii=False, indent=2)

def carregar():
    global CLIENTES
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            CLIENTES = json.load(f)
        log(f"{len(CLIENTES)} clientes carregados")

# ── Motor IA ──────────────────────────────────────────────────────────────
API_KEY  = "csk-6pc9f98ypfw5kp2hw9nv6ywt9n3j4hpe9h3dh2ww39k939cy"
API_URL  = "https://api.cerebras.ai/v1/chat/completions"
MODELO   = "gpt-oss-120b"

def gerar_resposta(cliente_id, pergunta):
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        return "Bot não configurado."

    cfg = cliente.get("config", {})
    key = os.environ.get("API_KEY", API_KEY).strip()
    if not key:
        return "Chave de IA não configurada no servidor."

    padroes   = cliente.get("padroes", [])
    correcoes = cliente.get("correcoes", [])

    pad_txt = ""
    if padroes:
        pad_txt = "\n\nEXEMPLOS DO ATENDENTE:\n" + "\n".join("- " + p for p in padroes[:15])

    cor_txt = ""
    if correcoes:
        exemplos = "\n".join(
            f'Pergunta: "{c["pergunta"]}" -> Resposta: "{c["correta"]}"'
            for c in correcoes[-10:]
        )
        cor_txt = "\n\nCORREÇÕES APRENDIDAS:\n" + exemplos

    system_prompt = (
        f"Você é {cfg.get('bot_nome', 'Assistente')}, "
        f"atendente virtual de {cfg.get('negocio_nome', 'um negócio')}.\n"
        f"Tipo: {cfg.get('negocio_tipo', '')}\n"
        f"Descrição: {cfg.get('negocio_descricao', '')}\n"
        f"Horário: {cfg.get('negocio_horario', '')}\n"
        f"Personalidade: {cfg.get('bot_personalidade', 'amigável e profissional')}\n\n"
        "Responda de forma natural e breve. Máximo 3 frases. Sem markdown."
        + pad_txt + cor_txt
    )

    historico = cliente.get("historico", {}).get(pergunta[:20], [])

    msgs = [{"role": "system", "content": system_prompt}]
    for h in historico[-4:]:
        msgs.append(h)
    msgs.append({"role": "user", "content": pergunta})

    modelo = cfg.get("modelo", MODELO) or MODELO
    payload = json.dumps({
        "model": modelo,
        "messages": msgs,
        "max_tokens": 200,
        "temperature": 0.7
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        log(f"Cerebras erro {e.code}: {body[:100]}")
        return "Desculpe, estou com dificuldades técnicas. Tente novamente em instantes."
    except Exception as ex:
        log(f"Cerebras ex: {ex}")
        return "Sem conexão com a IA no momento."

# ── Twilio: enviar mensagem ───────────────────────────────────────────────
def twilio_send(to, body, from_number, account_sid, auth_token):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode({
        "From": "whatsapp:" + from_number,
        "To":   "whatsapp:" + to,
        "Body": body
    }).encode("utf-8")

    import base64
    creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": "Basic " + creds,
        "Content-Type": "application/x-www-form-urlencoded"
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as ex:
        log(f"Twilio send erro: {ex}")
        return None

# ── HTML do Painel ────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsBot SaaS</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;--bg2:#161b22;--bg3:#1c2230;
  --border:#30363d;--green:#25d366;--red:#f85149;
  --blue:#58a6ff;--text:#e6edf3;--muted:#8b949e;--yellow:#e3b341;
}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;display:flex;height:100vh;overflow:hidden}
.sidebar{width:230px;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.logo{padding:20px 16px;border-bottom:1px solid var(--border)}
.logo h1{font-size:17px;color:var(--green);display:flex;align-items:center;gap:8px}
.logo p{font-size:11px;color:var(--muted);margin-top:3px}
.nav{padding:12px 0;flex:1;overflow-y:auto}
.nav a{display:flex;align-items:center;gap:10px;padding:10px 16px;color:var(--muted);text-decoration:none;font-size:13px;cursor:pointer;border-left:3px solid transparent;transition:.15s}
.nav a:hover,.nav a.active{color:var(--text);background:var(--bg3);border-left-color:var(--green)}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.topbar{padding:16px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:var(--bg2)}
.topbar h2{font-size:16px;font-weight:600}
.content{flex:1;overflow-y:auto;padding:24px}
.page{display:none}.page.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;text-align:center}
.card .num{font-size:28px;font-weight:700;color:var(--green)}
.card .lbl{font-size:12px;color:var(--muted);margin-top:4px}
.section{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px}
.section h3{font-size:14px;font-weight:600;margin-bottom:16px;color:var(--blue)}
.form-row{margin-bottom:12px}
.form-row label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.form-row input,.form-row select,.form-row textarea{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 12px;color:var(--text);font-size:13px;font-family:inherit;outline:none;transition:.2s}
.form-row input:focus,.form-row select:focus,.form-row textarea:focus{border-color:var(--green)}
.form-row textarea{min-height:80px;resize:vertical}
.btn{padding:10px 18px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:.15s}
.btn-green{background:var(--green);color:#000}.btn-green:hover{opacity:.85}
.btn-red{background:var(--red);color:#fff}.btn-red:hover{opacity:.85}
.btn-blue{background:var(--blue);color:#000}.btn-blue:hover{opacity:.85}
.btn-ghost{background:var(--bg3);color:var(--text);border:1px solid var(--border)}.btn-ghost:hover{border-color:var(--green)}
.badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600}
.badge-green{background:#1a3a2a;color:var(--green)}
.badge-red{background:#3a1a1a;color:var(--red)}
.badge-yellow{background:#3a2a0a;color:var(--yellow)}
.cliente-card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
.cliente-info h4{font-size:14px;font-weight:600}
.cliente-info p{font-size:12px;color:var(--muted);margin-top:2px}
.cliente-actions{display:flex;gap:8px}
.log-item{padding:8px 12px;border-bottom:1px solid var(--border);font-size:12px;display:flex;gap:12px}
.log-item .hora{color:var(--muted);white-space:nowrap}
.chat-area{display:flex;flex-direction:column;gap:10px;max-height:300px;overflow-y:auto;margin-bottom:12px;padding:4px}
.msg{max-width:75%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.5}
.msg-user{background:var(--green);color:#000;align-self:flex-end;border-radius:12px 12px 4px 12px}
.msg-bot{background:var(--bg3);border:1px solid var(--border);align-self:flex-start;border-radius:12px 12px 12px 4px}
.msg-input-row{display:flex;gap:8px}
.msg-input-row input{flex:1}
.toast{position:fixed;bottom:24px;right:24px;background:var(--green);color:#000;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;display:none;z-index:999}
.modal{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;z-index:100}
.modal.open{display:flex}
.modal-box{background:var(--bg2);border:1px solid var(--border);border-radius:16px;padding:28px;width:500px;max-width:95vw;max-height:90vh;overflow-y:auto}
.modal-box h3{font-size:16px;font-weight:700;margin-bottom:20px}
.modal-footer{display:flex;gap:8px;justify-content:flex-end;margin-top:20px}
</style>
</head>
<body>

<div class="sidebar">
  <div class="logo">
    <h1>🤖 WhatsBot</h1>
    <p>Painel SaaS</p>
  </div>
  <nav class="nav">
    <a onclick="goto('dashboard')" id="nav-dashboard" class="active">📊 Dashboard</a>
    <a onclick="goto('clientes')" id="nav-clientes">👥 Clientes</a>
    <a onclick="goto('testar')" id="nav-testar">💬 Testar Bot</a>
    <a onclick="goto('logs')" id="nav-logs">📋 Logs</a>
    <a onclick="goto('config')" id="nav-config">⚙️ Configuração</a>
  </nav>
</div>

<div class="main">
  <div class="topbar">
    <h2 id="page-title">Dashboard</h2>
    <span id="status-badge" class="badge badge-yellow">Carregando...</span>
  </div>
  <div class="content">

    <!-- DASHBOARD -->
    <div id="page-dashboard" class="page active">
      <div class="cards">
        <div class="card"><div class="num" id="d-clientes">0</div><div class="lbl">Clientes Ativos</div></div>
        <div class="card"><div class="num" id="d-msgs">0</div><div class="lbl">Msgs Respondidas Hoje</div></div>
        <div class="card"><div class="num" style="color:var(--blue)" id="d-modelo">—</div><div class="lbl">Modelo IA</div></div>
        <div class="card"><div class="num" style="color:var(--yellow)" id="d-uptime">—</div><div class="lbl">Servidor Online</div></div>
      </div>
      <div class="section">
        <h3>📋 Últimas Atividades</h3>
        <div id="d-logs"></div>
      </div>
    </div>

    <!-- CLIENTES -->
    <div id="page-clientes" class="page">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <span style="font-size:13px;color:var(--muted)" id="total-clientes">0 clientes</span>
        <button class="btn btn-green" onclick="abrirNovoCliente()">+ Novo Cliente</button>
      </div>
      <div id="lista-clientes"></div>
    </div>

    <!-- TESTAR -->
    <div id="page-testar" class="page">
      <div class="section">
        <h3>💬 Simular Conversa</h3>
        <div class="form-row">
          <label>Cliente</label>
          <select id="test-cliente"></select>
        </div>
        <div class="chat-area" id="chat-box"></div>
        <div class="msg-input-row">
          <input type="text" id="test-msg" placeholder="Digite uma mensagem..." onkeydown="if(event.key==='Enter')enviarTeste()">
          <button class="btn btn-green" onclick="enviarTeste()">Enviar</button>
        </div>
      </div>
    </div>

    <!-- LOGS -->
    <div id="page-logs" class="page">
      <div class="section">
        <h3>📋 Log do Sistema</h3>
        <div id="lista-logs"></div>
      </div>
    </div>

    <!-- CONFIG -->
    <div id="page-config" class="page">
      <div class="section">
        <h3>🔗 Twilio (WhatsApp)</h3>
        <div class="form-row"><label>Account SID</label><input type="text" id="cfg-sid" placeholder="ACxxxx..."></div>
        <div class="form-row"><label>Auth Token</label><input type="password" id="cfg-token" placeholder="sua_auth_token"></div>
        <div class="form-row"><label>Número Sandbox (ex: +14155238886)</label><input type="text" id="cfg-from" placeholder="+14155238886"></div>
      </div>
      <div class="section">
        <h3>🤖 IA</h3>
        <div class="form-row">
          <label>Modelo Cerebras</label>
          <select id="cfg-modelo">
            <option value="gpt-oss-120b">gpt-oss-120b (Recomendado)</option>
            <option value="zai-glm-4.7">zai-glm-4.7</option>
            <option value="gemma-4-31b">gemma-4-31b</option>
          </select>
        </div>
      </div>
      <button class="btn btn-green" onclick="salvarConfig()">💾 Salvar</button>
    </div>

  </div>
</div>

<!-- MODAL CLIENTE -->
<div class="modal" id="modal-cliente">
  <div class="modal-box">
    <h3 id="modal-titulo">Novo Cliente</h3>
    <input type="hidden" id="modal-id">
    <div class="form-row"><label>Nome do Negócio</label><input type="text" id="m-nome" placeholder="Ex: Barbearia do João"></div>
    <div class="form-row">
      <label>Tipo</label>
      <select id="m-tipo">
        <option value="">Selecione...</option>
        <option>Barbearia</option><option>Clínica odontológica</option>
        <option>Salão de beleza</option><option>Restaurante</option>
        <option>Loja de roupas</option><option>Pet shop</option>
        <option>Academia</option><option>Consultório médico</option><option>Outro</option>
      </select>
    </div>
    <div class="form-row"><label>Descrição (serviços, preços, endereço...)</label><textarea id="m-desc" placeholder="Ex: Corte R$25, Barba R$15. Rua das Flores, 123."></textarea></div>
    <div class="form-row"><label>Horário</label><input type="text" id="m-horario" placeholder="Ex: Seg-Sex 9h às 19h"></div>
    <div class="form-row"><label>Nome do Atendente Virtual</label><input type="text" id="m-bot-nome" placeholder="Ex: Ana, Carlos..."></div>
    <div class="form-row"><label>Personalidade</label><textarea id="m-personalidade" placeholder="Ex: Descontraído, usa emojis, chama pelo nome..."></textarea></div>
    <div class="form-row"><label>Número WhatsApp do Cliente (com DDI)</label><input type="text" id="m-numero" placeholder="Ex: +5544999998888"></div>
    <div class="form-row">
      <label>Treinar com conversa (cole aqui exportação do WhatsApp)</label>
      <textarea id="m-treino" placeholder="Cole aqui o histórico de conversas para o bot aprender o tom de voz..." style="min-height:100px"></textarea>
    </div>
    <div class="modal-footer">
      <button class="btn btn-ghost" onclick="fecharModal()">Cancelar</button>
      <button class="btn btn-green" onclick="salvarCliente()">Salvar</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let cfg_global = {};

async function api(path, method='GET', body=null){
  const opts = {method, headers:{'Content-Type':'application/json'}};
  if(body) opts.body = JSON.stringify(body);
  try{
    const r = await fetch(path, opts);
    return await r.json();
  }catch(e){ return {erro: e.message}; }
}

function toast(msg, cor='var(--green)'){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = cor;
  t.style.display = 'block';
  setTimeout(()=>t.style.display='none', 3000);
}

function goto(page){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav a').forEach(a=>a.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('active');
  document.getElementById('nav-'+page).classList.add('active');
  const titles = {dashboard:'Dashboard',clientes:'Clientes',testar:'Testar Bot',logs:'Logs',config:'Configuração'};
  document.getElementById('page-title').textContent = titles[page]||page;
  if(page==='dashboard') loadDash();
  if(page==='clientes') loadClientes();
  if(page==='testar') loadTestar();
  if(page==='logs') loadLogs();
  if(page==='config') loadConfig();
}

async function loadDash(){
  const d = await api('/api/dashboard');
  document.getElementById('d-clientes').textContent = d.clientes||0;
  document.getElementById('d-msgs').textContent = d.msgs_hoje||0;
  document.getElementById('d-modelo').textContent = d.modelo||'—';
  document.getElementById('d-uptime').textContent = d.uptime||'—';
  const badge = document.getElementById('status-badge');
  if(d.ia_ok){badge.textContent='IA Online';badge.className='badge badge-green';}
  else{badge.textContent='IA Offline';badge.className='badge badge-red';}
  const logs = d.logs||[];
  document.getElementById('d-logs').innerHTML = logs.length
    ? logs.slice(0,8).map(l=>`<div class="log-item"><span class="hora">${l.hora}</span><span>${l.msg}</span></div>`).join('')
    : '<p style="color:var(--muted);font-size:13px">Nenhuma atividade ainda.</p>';
}

async function loadClientes(){
  const d = await api('/api/clientes');
  const lista = d.clientes||[];
  document.getElementById('total-clientes').textContent = lista.length + ' cliente(s)';
  document.getElementById('lista-clientes').innerHTML = lista.length
    ? lista.map(c=>`
      <div class="cliente-card">
        <div class="cliente-info">
          <h4>${c.nome}</h4>
          <p>${c.tipo||'Sem tipo'} • ${c.numero||'Sem número'}</p>
        </div>
        <div class="cliente-actions">
          <button class="btn btn-ghost" style="font-size:11px;padding:6px 10px" onclick="editarCliente('${c.id}')">✏️ Editar</button>
          <button class="btn btn-red" style="font-size:11px;padding:6px 10px" onclick="removerCliente('${c.id}')">🗑️</button>
        </div>
      </div>`).join('')
    : '<p style="color:var(--muted);font-size:13px;text-align:center;padding:40px">Nenhum cliente ainda. Clique em "+ Novo Cliente".</p>';
}

async function loadTestar(){
  const d = await api('/api/clientes');
  const sel = document.getElementById('test-cliente');
  sel.innerHTML = (d.clientes||[]).map(c=>`<option value="${c.id}">${c.nome}</option>`).join('');
  document.getElementById('chat-box').innerHTML = '';
}

async function loadLogs(){
  const d = await api('/api/logs');
  const logs = d.logs||[];
  document.getElementById('lista-logs').innerHTML = logs.length
    ? logs.map(l=>`<div class="log-item"><span class="hora">${l.hora}</span><span>${l.msg}</span></div>`).join('')
    : '<p style="color:var(--muted);font-size:13px">Nenhum log ainda.</p>';
}

async function loadConfig(){
  const d = await api('/api/config');
  document.getElementById('cfg-sid').value = d.twilio_sid||'';
  document.getElementById('cfg-token').value = d.twilio_token||'';
  document.getElementById('cfg-from').value = d.twilio_from||'';
  document.getElementById('cfg-modelo').value = d.modelo||'gpt-oss-120b';
  cfg_global = d;
}

async function salvarConfig(){
  const r = await api('/api/config','POST',{
    twilio_sid: document.getElementById('cfg-sid').value.trim(),
    twilio_token: document.getElementById('cfg-token').value.trim(),
    twilio_from: document.getElementById('cfg-from').value.trim(),
    modelo: document.getElementById('cfg-modelo').value,
  });
  if(r.ok) toast('Configurações salvas!');
  else toast('Erro ao salvar', 'var(--red)');
}

function abrirNovoCliente(){
  document.getElementById('modal-titulo').textContent = 'Novo Cliente';
  document.getElementById('modal-id').value = '';
  ['m-nome','m-tipo','m-desc','m-horario','m-bot-nome','m-personalidade','m-numero','m-treino'].forEach(id=>{
    const el = document.getElementById(id);
    if(el) el.value='';
  });
  document.getElementById('modal-cliente').classList.add('open');
}

async function editarCliente(id){
  const d = await api('/api/cliente/'+id);
  if(d.erro){toast('Erro ao carregar','var(--red)');return;}
  document.getElementById('modal-titulo').textContent = 'Editar Cliente';
  document.getElementById('modal-id').value = id;
  document.getElementById('m-nome').value = d.config?.negocio_nome||'';
  document.getElementById('m-tipo').value = d.config?.negocio_tipo||'';
  document.getElementById('m-desc').value = d.config?.negocio_descricao||'';
  document.getElementById('m-horario').value = d.config?.negocio_horario||'';
  document.getElementById('m-bot-nome').value = d.config?.bot_nome||'';
  document.getElementById('m-personalidade').value = d.config?.bot_personalidade||'';
  document.getElementById('m-numero').value = d.config?.numero||'';
  document.getElementById('m-treino').value = '';
  document.getElementById('modal-cliente').classList.add('open');
}

function fecharModal(){
  document.getElementById('modal-cliente').classList.remove('open');
}

async function salvarCliente(){
  const id = document.getElementById('modal-id').value;
  const body = {
    negocio_nome: document.getElementById('m-nome').value.trim(),
    negocio_tipo: document.getElementById('m-tipo').value,
    negocio_descricao: document.getElementById('m-desc').value.trim(),
    negocio_horario: document.getElementById('m-horario').value.trim(),
    bot_nome: document.getElementById('m-bot-nome').value.trim()||'Assistente',
    bot_personalidade: document.getElementById('m-personalidade').value.trim(),
    numero: document.getElementById('m-numero').value.trim(),
    treino: document.getElementById('m-treino').value.trim(),
    id: id||null,
  };
  const r = await api('/api/cliente','POST', body);
  if(r.ok){
    toast('Cliente salvo!');
    fecharModal();
    loadClientes();
  } else {
    toast('Erro: '+(r.erro||'desconhecido'), 'var(--red)');
  }
}

async function removerCliente(id){
  if(!confirm('Remover este cliente?')) return;
  const r = await api('/api/cliente/'+id,'DELETE');
  if(r.ok){ toast('Removido'); loadClientes(); }
}

async function enviarTeste(){
  const cid = document.getElementById('test-cliente').value;
  const msg = document.getElementById('test-msg').value.trim();
  if(!msg||!cid) return;
  document.getElementById('test-msg').value='';
  const box = document.getElementById('chat-box');
  box.innerHTML += `<div class="msg msg-user">${msg}</div>`;
  box.scrollTop=box.scrollHeight;
  const r = await api('/api/chat','POST',{cliente_id:cid,mensagem:msg});
  box.innerHTML += `<div class="msg msg-bot">${r.resposta||'Erro ao gerar resposta'}</div>`;
  box.scrollTop=box.scrollHeight;
}

loadDash();
setInterval(()=>{
  if(document.getElementById('page-dashboard').classList.contains('active')) loadDash();
},10000);
</script>
</body>
</html>"""

# ── Handler HTTP ──────────────────────────────────────────────────────────
import uuid
import time

START_TIME = time.time()
MSGS_HOJE = [0]
SERVER_CONFIG = {}
CONFIG_FILE_SERVER = "server_config.json"

def carregar_server_config():
    global SERVER_CONFIG
    if os.path.exists(CONFIG_FILE_SERVER):
        with open(CONFIG_FILE_SERVER, encoding="utf-8") as f:
            SERVER_CONFIG = json.load(f)

def salvar_server_config():
    with open(CONFIG_FILE_SERVER, "w", encoding="utf-8") as f:
        json.dump(SERVER_CONFIG, f, ensure_ascii=False, indent=2)

def processar_treino(conteudo):
    lines = conteudo.split("\n")
    msgs = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.search(r'\[\d{1,2}/\d{1,2}/\d{2,4}.*?\]\s*([^:]+?):\s*(.+)$', line)
        if not m:
            m = re.search(r'\d{1,2}/\d{1,2}/\d{2,4}.*?[-\u2013]\s*([^:]+?):\s*(.+)$', line)
        if not m:
            m = re.search(r'^([A-Za-z\u00C0-\u024F ]{2,30}):\s*(.{5,})$', line)
        if m:
            msgs.append({"remetente": m.group(1).strip(), "texto": m.group(2).strip()})

    if len(msgs) < 3:
        return []

    contagem = Counter(m["remetente"] for m in msgs)
    atendente = contagem.most_common(1)[0][0]
    padroes = []
    for m in msgs:
        if m["remetente"] == atendente and len(m["texto"]) > 10:
            padroes.append('Exemplo de resposta: "' + m["texto"][:80] + '"')
    return padroes[:30]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]

        if p == "/" or p == "/index.html":
            self._html(HTML)

        elif p == "/api/dashboard":
            uptime_s = int(time.time() - START_TIME)
            h, m = divmod(uptime_s // 60, 60)
            self._json({
                "clientes": len(CLIENTES),
                "msgs_hoje": MSGS_HOJE[0],
                "modelo": SERVER_CONFIG.get("modelo", "gpt-oss-120b"),
                "uptime": f"{h}h {m}m",
                "ia_ok": bool(os.environ.get("CEREBRAS_KEY")),
                "logs": LOGS[:8],
            })

        elif p == "/api/clientes":
            lista = [{"id": cid, "nome": c["config"].get("negocio_nome",""), "tipo": c["config"].get("negocio_tipo",""), "numero": c["config"].get("numero","")} for cid, c in CLIENTES.items()]
            self._json({"clientes": lista})

        elif p.startswith("/api/cliente/"):
            cid = p.split("/")[-1]
            if cid in CLIENTES:
                self._json(CLIENTES[cid])
            else:
                self._json({"erro": "não encontrado"}, 404)

        elif p == "/api/config":
            self._json(SERVER_CONFIG)

        elif p == "/api/logs":
            self._json({"logs": LOGS})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        p = self.path.split("?")[0]

        if p == "/api/cliente":
            data = self._body()
            cid = data.get("id") or str(uuid.uuid4())[:8]
            treino_txt = data.pop("treino", "") or ""
            padroes_novos = processar_treino(treino_txt) if treino_txt else []

            if cid not in CLIENTES:
                CLIENTES[cid] = {"config": {}, "padroes": [], "correcoes": [], "historico": {}}

            CLIENTES[cid]["config"] = {k: v for k, v in data.items() if k != "id"}
            if padroes_novos:
                CLIENTES[cid]["padroes"] = padroes_novos + CLIENTES[cid]["padroes"]
                CLIENTES[cid]["padroes"] = list(dict.fromkeys(CLIENTES[cid]["padroes"]))[:100]
            salvar()
            log(f"Cliente salvo: {data.get('negocio_nome','?')} (id={cid})")
            self._json({"ok": True, "id": cid})

        elif p == "/api/config":
            data = self._body()
            SERVER_CONFIG.update(data)
            salvar_server_config()
            self._json({"ok": True})

        elif p == "/api/chat":
            data = self._body()
            cid = data.get("cliente_id", "")
            msg = data.get("mensagem", "")
            if not cid or not msg:
                self._json({"erro": "dados incompletos"})
                return
            resposta = gerar_resposta(cid, msg)
            MSGS_HOJE[0] += 1
            log(f"Chat [{cid}]: '{msg[:40]}' → '{resposta[:40]}'")
            self._json({"resposta": resposta})

        elif p == "/webhook/twilio":
            # Twilio envia form-encoded
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            params = dict(urllib.parse.parse_qsl(raw))

            from_num = params.get("From", "").replace("whatsapp:", "")
            body_msg = params.get("Body", "").strip()

            log(f"Twilio IN: {from_num} → '{body_msg[:50]}'")

            # Encontra o cliente pelo número
            cliente_id = None
            for cid, c in CLIENTES.items():
                if c["config"].get("numero", "") == from_num:
                    cliente_id = cid
                    break

            if not cliente_id and CLIENTES:
                # Usa o primeiro cliente ativo se número não vinculado
                cliente_id = next(iter(CLIENTES))
                log(f"Número não vinculado — usando cliente padrão: {cliente_id}")

            if not cliente_id:
                twiml = '<?xml version="1.0"?><Response><Message>Olá! Nenhum cliente configurado ainda.</Message></Response>'
            else:
                resposta = gerar_resposta(cliente_id, body_msg)
                MSGS_HOJE[0] += 1
                log(f"Twilio OUT [{cliente_id}]: '{resposta[:50]}'")
                twiml = f'<?xml version="1.0"?><Response><Message>{resposta}</Message></Response>'

            body = twiml.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/cliente/"):
            cid = p.split("/")[-1]
            if cid in CLIENTES:
                del CLIENTES[cid]
                salvar()
                log(f"Cliente removido: {cid}")
                self._json({"ok": True})
            else:
                self._json({"erro": "não encontrado"}, 404)
        else:
            self.send_response(404)
            self.end_headers()

# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    carregar()
    carregar_server_config()
    PORT = int(os.environ.get("PORT", 8080))
    print(f"WhatsBot SaaS rodando na porta {PORT}")
    print(f"IA: {'OK - ' + os.environ.get('CEREBRAS_KEY','')[:8] + '...' if os.environ.get('CEREBRAS_KEY') else 'SEM CHAVE'}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

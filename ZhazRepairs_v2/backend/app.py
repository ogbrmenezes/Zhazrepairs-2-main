import os, sqlite3
from flask import Flask, render_template, request, jsonify, session, redirect
from auth_roles import require_login, require_roles, now_sp_str
from enviar import send_email

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY','dev-secret')
DB_PATH = os.path.join(os.path.dirname(__file__), 'zhaz.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; return conn

def migrate():
    conn = get_conn(); c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS usuarios(id INTEGER PRIMARY KEY AUTOINCREMENT,nome TEXT,email TEXT UNIQUE,senha TEXT,papel TEXT NOT NULL,created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS tecnicos(id INTEGER PRIMARY KEY AUTOINCREMENT,nome TEXT, funcao TEXT, email TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS os(id INTEGER PRIMARY KEY AUTOINCREMENT,os_numero TEXT UNIQUE,equipamento TEXT,defeito TEXT,tecnico_entregou_id INTEGER,status TEXT,data_registro TEXT,sla_inicio TEXT,pego_por_admin_em TEXT,liberado_teste_em TEXT,resultado_em TEXT,resultado TEXT,aguardando_reposicao INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS historico_remanejamento(id INTEGER PRIMARY KEY AUTOINCREMENT,os_origem TEXT,os_destino TEXT,componente TEXT,observacao TEXT,data_registro TEXT,responsavel_email TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS status_history(id INTEGER PRIMARY KEY AUTOINCREMENT,os_id INTEGER NOT NULL,status TEXT NOT NULL,changed_at TEXT NOT NULL,changed_by TEXT,obs TEXT)")
    conn.commit(); conn.close()

def seed():
    conn = get_conn(); c = conn.cursor()
    # Rodrigo (Admin) e um login de diretoria
    c.execute("SELECT id FROM usuarios WHERE email=?", ('rodrigo.oliveira@zhaz.com.br',))
    if not c.fetchone():
        c.execute("INSERT INTO usuarios(nome,email,senha,papel,created_at) VALUES(?,?,?,?,?)",
                  ('Rodrigo Oliveira','rodrigo.oliveira@zhaz.com.br','123','ADMIN', now_sp_str()))

@app.route('/')
def home(): 
    return redirect('/login')

@app.route('/login')
def login_page(): 
    return render_template('login.html')

# --- Login (3 perfis) ---
@app.post('/login/admin')
def login_admin():
    email=request.json.get('email','').strip().lower(); senha=request.json.get('senha','')
    c=get_conn().cursor(); c.execute("SELECT nome, senha FROM usuarios WHERE email=? AND papel='ADMIN'", (email,)); row=c.fetchone(); c.connection.close()
    if not row or row[1]!=senha: return (jsonify({'erro':'Credenciais inválidas'}),401)
    session['usuario']=email; session['papel']='ADMIN'; return jsonify({'ok':True})

@app.post('/login/diretoria')
def login_diretoria():
    email=request.json.get('email','').strip().lower(); senha=request.json.get('senha','')
    c=get_conn().cursor(); c.execute("SELECT nome, senha FROM usuarios WHERE email=? AND papel='DIRETORIA'", (email,)); row=c.fetchone(); c.connection.close()
    if not row or row[1]!=senha: return (jsonify({'erro':'Credenciais inválidas'}),401)
    session['usuario']=email; session['papel']='DIRETORIA'; return jsonify({'ok':True})

@app.post('/login/tecnico')
def login_tecnico():
    nome=request.json.get('nome','').strip(); email=request.json.get('email','').strip().lower()
    if not nome or not email: return (jsonify({'erro':'Nome e e-mail são obrigatórios'}),400)
    conn=get_conn(); cur=conn.cursor(); cur.execute("SELECT id,papel FROM usuarios WHERE email=?", (email,)); u=cur.fetchone()
    if not u:
        cur.execute("INSERT INTO usuarios(nome,email,senha,papel,created_at) VALUES(?,?,?,?,?)", (nome,email,'','TECNICO',now_sp_str()))
    elif u['papel'] not in ('TECNICO','COMUM',''):
        conn.close(); return (jsonify({'erro':'E-mail já utilizado por outro perfil'}),409)
    cur.execute("SELECT id FROM tecnicos WHERE email=?", (email,))
    if not cur.fetchone():
        cur.execute("INSERT INTO tecnicos(nome,funcao,email) VALUES(?, 'Tecnico', ?)", (nome,email))
    conn.commit(); conn.close(); session['usuario']=email; session['papel']='TECNICO'; session['tecnico_nome']=nome; return jsonify({'ok':True})

# --- Páginas ---
@app.route('/registrar_os', methods=['GET'])
@require_login
@require_roles('TECNICO')
def registrar_os_page(): 
    return render_template('registrar_os.html')

# técnico/admin cadastra → inicia SLA
@app.route('/registrar_os', methods=['POST'])
@require_login
@require_roles('TECNICO')
def registrar_os_post():
    data=request.json or {}; os_numero=data.get('os_numero'); equipamento=data.get('equipamento'); defeito=data.get('defeito',''); tecnico_email=session.get('usuario')
    conn=get_conn(); cur=conn.cursor(); cur.execute("SELECT id FROM tecnicos WHERE email=?", (tecnico_email,)); tec=cur.fetchone()
    if not tec: conn.close(); return (jsonify({'erro':'Técnico não encontrado'}),400)
    agora=now_sp_str()
    cur.execute("INSERT INTO os(os_numero,equipamento,defeito,tecnico_entregou_id,status,data_registro) VALUES (?,?,?,?, 'Em atenção', ?)", (os_numero,equipamento,defeito,tec['id'],agora))
    os_id=cur.lastrowid; cur.execute("UPDATE os SET sla_inicio=? WHERE id=?", (agora, os_id))
    cur.execute("INSERT INTO status_history(os_id,status,changed_at,changed_by,obs) VALUES (?,?,?,?,?)", (os_id,'Em atenção',agora,tecnico_email,'Início SLA'))
    conn.commit(); conn.close(); return jsonify({'ok':True,'id':os_id})


# === Técnico: visualizar e concluir OS liberadas para teste ===
@app.route('/minhas_os')
@require_login
@require_roles('TECNICO')
def minhas_os_page():
    return render_template('minhas_os.html')

@app.get('/api/minhas_os')
@require_login
@require_roles('TECNICO')
def api_minhas_os():
    email = session.get('usuario')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT o.id, o.os_numero, o.equipamento, o.status,
               o.data_registro, o.liberado_teste_em, o.resultado_em, o.resultado
        FROM os o
        JOIN tecnicos t ON t.id = o.tecnico_entregou_id
        WHERE t.email = ?
        ORDER BY o.data_registro DESC
    """, (email,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


# Rodrigo pega OS
@app.route('/os/<int:os_id>/pegar', methods=['POST'])
@require_login
@require_roles('ADMIN')
def pegar_os(os_id):
    conn=get_conn(); cur=conn.cursor(); agora=now_sp_str()
    cur.execute("UPDATE os SET pego_por_admin_em=COALESCE(pego_por_admin_em, ?), status='Em atenção' WHERE id=?", (agora, os_id))
    cur.execute("INSERT INTO status_history(os_id,status,changed_at,changed_by,obs) VALUES (?,?,?,?,?)", (os_id,'Em atenção',agora,session.get('usuario'),'Rodrigo pegou a OS'))
    conn.commit(); conn.close(); return jsonify({'ok':True})

# Rodrigo libera para teste
@app.route('/liberar_para_teste', methods=['POST'])
@require_login
@require_roles('ADMIN')
def liberar_para_teste():
    os_id=request.json.get('os_id'); conn=get_conn(); cur=conn.cursor(); agora=now_sp_str()
    cur.execute("UPDATE os SET status='Liberada para teste', liberado_teste_em=COALESCE(liberado_teste_em, ?), pego_por_admin_em=COALESCE(pego_por_admin_em, ?) WHERE id=?", (agora,agora,os_id))
    cur.execute("INSERT INTO status_history(os_id,status,changed_at,changed_by,obs) VALUES (?,?,?,?,?)", (os_id,'Liberada para teste',agora,session.get('usuario'),'Liberada por Rodrigo'))
    cur.execute("SELECT t.email, o.os_numero FROM os o JOIN tecnicos t ON t.id=o.tecnico_entregou_id WHERE o.id=?", (os_id,)); r=cur.fetchone()
    if r: send_email(r['email'], f"OS {r['os_numero']} liberada para teste", "A peça foi liberada para testes.")
    conn.commit(); conn.close(); return jsonify({'ok':True})

# Técnico conclui
@app.route('/marcar_sucesso', methods=['POST'])
@require_login
@require_roles('TECNICO')
def marcar_sucesso():
    os_id=request.json.get('os_id'); conn=get_conn(); cur=conn.cursor(); agora=now_sp_str()
    cur.execute("UPDATE os SET status='Reparada', resultado='REPARADA', resultado_em=COALESCE(resultado_em, ?) WHERE id=?", (agora, os_id))
    cur.execute("INSERT INTO status_history(os_id,status,changed_at,changed_by,obs) VALUES (?,?,?,?,?)", (os_id,'Reparada',agora,session.get('usuario'),'Técnico concluiu: Reparada'))
    conn.commit(); conn.close(); return jsonify({'ok':True})

@app.route('/marcar_sem_reparo', methods=['POST'])
@require_login
@require_roles('TECNICO')
def marcar_sem_reparo():
    os_id=request.json.get('os_id'); conn=get_conn(); cur=conn.cursor(); agora=now_sp_str()
    cur.execute("UPDATE os SET status='Não reparada', resultado='NAO_REPARADA', resultado_em=COALESCE(resultado_em, ?) WHERE id=?", (agora, os_id))
    cur.execute("INSERT INTO status_history(os_id,status,changed_at,changed_by,obs) VALUES (?,?,?,?,?)", (os_id,'Não reparada',agora,session.get('usuario'),'Técnico concluiu: Sem reparo'))
    conn.commit(); conn.close(); return jsonify({'ok':True})

# Remanejamento
@app.route('/remanejar', methods=['POST'])
@require_login
@require_roles('TECNICO','ADMIN')
def remanejar():
    data=request.json or {}; os_origem=data.get('os_origem'); os_destino=data.get('os_destino'); componente=data.get('componente',''); obs=data.get('observacao',''); agora=now_sp_str()
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO historico_remanejamento(os_origem,os_destino,componente,observacao,data_registro,responsavel_email) VALUES (?,?,?,?,?,?)", (os_origem,os_destino,componente,obs,agora,session.get('usuario')))
    cur.execute("UPDATE os SET aguardando_reposicao=1 WHERE os_numero=?", (os_origem,))
    conn.commit(); conn.close(); return jsonify({'ok':True})

@app.route('/os/<string:os_numero>/reposicao-concluida', methods=['POST'])
@require_login
@require_roles('ADMIN')
def reposicao_concluida(os_numero):
    conn=get_conn(); cur=conn.cursor(); cur.execute("UPDATE os SET aguardando_reposicao=0 WHERE os_numero=?", (os_numero,)); conn.commit(); conn.close(); return jsonify({'ok':True})

# Métricas p/ portal
@app.route('/api/metrics/por_modelo')
@require_login
@require_roles('ADMIN','DIRETORIA')
def m_por_modelo():
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT equipamento, SUM(CASE WHEN resultado='REPARADA' THEN 1 ELSE 0 END), SUM(CASE WHEN resultado='NAO_REPARADA' THEN 1 ELSE 0 END), COUNT(*) FROM os WHERE resultado IN ('REPARADA','NAO_REPARADA') GROUP BY equipamento ORDER BY 4 DESC")
    rows=[dict(equipamento=r[0],reparadas=r[1],nao_reparadas=r[2],total=r[3]) for r in cur.fetchall()]; conn.close(); return jsonify(rows)



# ====== Métricas por reparador (Admin que libera para teste) ======
@app.get("/api/metrics/reparador")
@require_login
@require_roles('DIRETORIA','ADMIN')
def metrics_reparador():
    # pode vir por query ?reparador=email  | se não vier e for ADMIN, usa o usuário logado
    reparador = (request.args.get("reparador") or "").strip().lower()
    if not reparador and session.get('papel') == 'ADMIN':
        reparador = (session.get('usuario') or "").lower()

    if not reparador:
        return jsonify({"erro":"Informe ?reparador=<email> ou faça login como ADMIN"}), 400

    conn = get_conn(); cur = conn.cursor()

    # OS tratadas por esse reparador: as que ele marcou "Liberada para teste"
    cur.execute("""
        SELECT DISTINCT h.os_id
        FROM status_history h
        WHERE h.status='Liberada para teste' AND LOWER(h.changed_by)=?
    """, (reparador,))
    os_ids = [r[0] for r in cur.fetchall()]
    if not os_ids:
        conn.close()
        return jsonify({
            "reparador": {"email": reparador},
            "cards": {"total": 0, "reparadas": 0, "nao_reparadas": 0, "taxa_sucesso_pct": 0.0},
            "time": {"mttr_dias": 0.0, "lead_time_30d": 0.0},
            "trend": [], "top_equipamentos": [], "last_activities": []
        })

    ids_tuple = tuple(os_ids)
    where_ids = f"IN ({','.join(['?']*len(os_ids))})"

    # contagens finais das OS que ele liberou
    cur.execute(f"""
        SELECT
          SUM(CASE WHEN resultado='REPARADA'     THEN 1 ELSE 0 END) AS reparadas,
          SUM(CASE WHEN resultado='NAO_REPARADA' THEN 1 ELSE 0 END) AS nao_reparadas,
          COUNT(*) AS total
        FROM os
        WHERE id {where_ids}
    """, os_ids)
    c = cur.fetchone()
    total = c["total"] or 0
    rep  = c["reparadas"] or 0
    nrep = c["nao_reparadas"] or 0
    taxa = (rep/total*100.0) if total else 0.0

    # MTTR/Lead time sobre as OS que ele tratou
    cur.execute(f"""
        SELECT AVG(julianday(resultado_em) - julianday(sla_inicio))
        FROM os
        WHERE id {where_ids} AND resultado_em IS NOT NULL AND sla_inicio IS NOT NULL
    """, os_ids)
    mttr = cur.fetchone()[0] or 0.0

    cur.execute(f"""
        SELECT AVG(julianday(resultado_em) - julianday(data_registro))
        FROM os
        WHERE id {where_ids} AND resultado_em IS NOT NULL
          AND date(resultado_em) >= date('now','localtime','-30 day')
    """, os_ids)
    lead30 = cur.fetchone()[0] or 0.0

    # tendência (14 dias) considerando as OS dele: entradas e saídas dessas OS
    cur.execute(f"""
    WITH dias AS (
      SELECT date('now','localtime','-13 day') d
      UNION ALL SELECT date('now','localtime','-12 day')
      UNION ALL SELECT date('now','localtime','-11 day')
      UNION ALL SELECT date('now','localtime','-10 day')
      UNION ALL SELECT date('now','localtime','-9 day')
      UNION ALL SELECT date('now','localtime','-8 day')
      UNION ALL SELECT date('now','localtime','-7 day')
      UNION ALL SELECT date('now','localtime','-6 day')
      UNION ALL SELECT date('now','localtime','-5 day')
      UNION ALL SELECT date('now','localtime','-4 day')
      UNION ALL SELECT date('now','localtime','-3 day')
      UNION ALL SELECT date('now','localtime','-2 day')
      UNION ALL SELECT date('now','localtime','-1 day')
      UNION ALL SELECT date('now','localtime')
    )
    SELECT
      d AS dia,
      (SELECT COUNT(*) FROM os o WHERE o.id {where_ids} AND date(o.data_registro)=d) AS entradas,
      (SELECT COUNT(*) FROM os o WHERE o.id {where_ids} AND date(COALESCE(o.resultado_em,''))=d) AS saidas
    FROM dias
    """, os_ids*2)
    trend = [dict(r) for r in cur.fetchall()]

    # top equipamentos (nas OS dele) por taxa de sucesso
    cur.execute(f"""
        SELECT
          equipamento,
          SUM(CASE WHEN resultado='REPARADA'     THEN 1 ELSE 0 END) AS reparadas,
          SUM(CASE WHEN resultado='NAO_REPARADA' THEN 1 ELSE 0 END) AS nao_reparadas,
          COUNT(*) AS total
        FROM os
        WHERE id {where_ids}
        GROUP BY equipamento
        HAVING total >= 3
        ORDER BY (CAST(reparadas AS REAL)/NULLIF(total,0)) DESC, total DESC
        LIMIT 10
    """, os_ids)
    top_eqp = [dict(r) for r in cur.fetchall()]

    # últimas atividades do reparador (o que ele próprio registrou)
    cur.execute("""
        SELECT h.os_id, o.os_numero AS numero, h.status, datetime(h.changed_at) AS quando
        FROM status_history h
        JOIN os o ON o.id=h.os_id
        WHERE LOWER(h.changed_by)=?
        ORDER BY h.changed_at DESC
        LIMIT 25
    """, (reparador,))
    last = [dict(r) for r in cur.fetchall()]

    conn.close()
    return jsonify({
        "reparador": {"email": reparador},
        "cards": {"total": total, "reparadas": rep, "nao_reparadas": nrep, "taxa_sucesso_pct": round(taxa,1)},
        "time": {"mttr_dias": mttr, "lead_time_30d": lead30},
        "trend": trend,
        "top_equipamentos": top_eqp,
        "last_activities": last,
    })




@app.route('/api/metrics/ranking_tecnicos')
@require_login
@require_roles('ADMIN','DIRETORIA')
def m_rank():
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT t.id,t.nome,t.email,COUNT(o.id) FROM tecnicos t JOIN os o ON o.tecnico_entregou_id=t.id GROUP BY t.id,t.nome,t.email ORDER BY COUNT(o.id) DESC,t.nome ASC")
    rows=[dict(tecnico_id=r[0],nome=r[1],email=r[2],entregas=r[3]) for r in cur.fetchall()]; conn.close(); return jsonify(rows)

@app.route('/api/metrics/sla_resumo')
@require_login
@require_roles('ADMIN','DIRETORIA')
def m_sla():
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT id, os_numero, equipamento, sla_inicio, pego_por_admin_em, liberado_teste_em, resultado_em, resultado FROM os WHERE sla_inicio IS NOT NULL")
    rows=cur.fetchall(); conn.close()
    from datetime import datetime; import pytz
    tz=pytz.timezone('America/Sao_Paulo'); parse=lambda s: tz.localize(datetime.strptime(s,'%Y-%m-%d %H:%M:%S')) if s else None
    out=[]
    for r in rows:
        t1,t2,t3,t4=map(parse,[r['sla_inicio'],r['pego_por_admin_em'],r['liberado_teste_em'],r['resultado_em']])
        d={'id':r['id'],'os':r['os_numero'],'equipamento':r['equipamento'],'resultado':r['resultado'],'t_tec_para_admin_h':None,'t_admin_reparo_h':None,'t_teste_h':None}
        if t1 and t2: d['t_tec_para_admin_h']=round((t2-t1).total_seconds()/3600,2)
        if t2 and t3: d['t_admin_reparo_h']=round((t3-t2).total_seconds()/3600,2)
        if t3 and t4: d['t_teste_h']=round((t4-t3).total_seconds()/3600,2)
        out.append(d)
    return jsonify(out)

@app.route('/api/metrics/rem_aguardando')
@require_login
@require_roles('ADMIN','DIRETORIA')
def m_rem_aguardando():
    conn=get_conn(); cur=conn.cursor(); cur.execute("SELECT id,os_numero,equipamento,data_registro FROM os WHERE aguardando_reposicao=1 ORDER BY data_registro ASC")
    rows=[dict(id=r[0],os=r[1],equipamento=r[2],data_registro=r[3]) for r in cur.fetchall()]; conn.close(); return jsonify(rows)

# ===== Dashboard (página) =====
@app.route('/dashboard')
@require_login
@require_roles('ADMIN','DIRETORIA')
def dashboard():
    return render_template('dashboard.html')  # precisa existir templates/dashboard.html

# Helpers para consultas rápidas (usam seu get_conn())
from flask import jsonify
def q_rows(sql, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def q_one(sql, params=(), default=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.close()
    return (row[0] if row and row[0] is not None else default)

# ===== API do dashboard =====
@app.route('/api/metrics')
@require_login
@require_roles('ADMIN','DIRETORIA')
def metrics():
    # Cards por status e total
    st = q_rows("""
        SELECT
          SUM(CASE WHEN status='Em atenção'            THEN 1 ELSE 0 END) AS em_atencao,
          SUM(CASE WHEN status='Liberada para teste'   THEN 1 ELSE 0 END) AS liberada_teste,
          SUM(CASE WHEN status='Reparada'              THEN 1 ELSE 0 END) AS reparada,
          SUM(CASE WHEN status='Não reparada'          THEN 1 ELSE 0 END) AS nao_reparada,
          COUNT(*) AS total
        FROM os
    """)
    st = dict(zip(
        ["em_atencao","liberada_teste","reparada","nao_reparada","total"],
        list(st[0]) if st else [0,0,0,0,0]
    ))

    today = q_rows("""
        SELECT
          SUM(CASE WHEN date(data_registro)=date('now','localtime') THEN 1 ELSE 0 END) AS entradas_hoje,
          SUM(CASE WHEN date(COALESCE(resultado_em,''))=date('now','localtime') THEN 1 ELSE 0 END) AS saidas_hoje
        FROM os
    """)
    today = dict(today[0]) if today else {"entradas_hoje":0,"saidas_hoje":0}

    # Tendência 14 dias
    trend = q_rows("""
    WITH dias AS (
      SELECT date('now','localtime','-13 day') d
      UNION ALL SELECT date('now','localtime','-12 day')
      UNION ALL SELECT date('now','localtime','-11 day')
      UNION ALL SELECT date('now','localtime','-10 day')
      UNION ALL SELECT date('now','localtime','-9 day')
      UNION ALL SELECT date('now','localtime','-8 day')
      UNION ALL SELECT date('now','localtime','-7 day')
      UNION ALL SELECT date('now','localtime','-6 day')
      UNION ALL SELECT date('now','localtime','-5 day')
      UNION ALL SELECT date('now','localtime','-4 day')
      UNION ALL SELECT date('now','localtime','-3 day')
      UNION ALL SELECT date('now','localtime','-2 day')
      UNION ALL SELECT date('now','localtime','-1 day')
      UNION ALL SELECT date('now','localtime')
    )
    SELECT
      d AS dia,
      (SELECT COUNT(*) FROM os o WHERE date(o.data_registro)=d) AS entradas,
      (SELECT COUNT(*) FROM os o WHERE date(COALESCE(o.resultado_em,''))=d) AS saidas
    FROM dias
    """)

    # Atividades recentes
    last = q_rows("""
        SELECT h.os_id, o.os_numero AS numero, h.status, datetime(h.changed_at) AS quando
        FROM status_history h
        JOIN os o ON o.id = h.os_id
        ORDER BY h.changed_at DESC
        LIMIT 25
    """)

    # MTTR: média (resultado_em - sla_inicio) nas concluídas
    mttr = q_one("""
        SELECT AVG(julianday(resultado_em) - julianday(sla_inicio))
        FROM os
        WHERE resultado_em IS NOT NULL AND sla_inicio IS NOT NULL
    """, default=0.0)

    # Lead time 30d: média (resultado_em - data_registro) concluídas nos últimos 30 dias
    lead_30 = q_one("""
        SELECT AVG(julianday(resultado_em) - julianday(data_registro))
        FROM os
        WHERE resultado_em IS NOT NULL
          AND date(resultado_em) >= date('now','localtime','-30 day')
    """, default=0.0)

    # Aging por status (abertas)
    aging = q_rows("""
        SELECT status, AVG(julianday('now','localtime') - julianday(data_registro)) AS dias
        FROM os
        WHERE resultado_em IS NULL
        GROUP BY status
    """)
    aging_map = { r["status"]: r["dias"] for r in aging }

    # “SLA próximos 2 dias” (adaptação): liberadas p/ teste há >= 2 dias sem resultado
    sla_48h = q_one("""
        SELECT COUNT(*)
        FROM os
        WHERE status='Liberada para teste'
          AND resultado_em IS NULL
          AND liberado_teste_em IS NOT NULL
          AND (julianday('now','localtime') - julianday(liberado_teste_em)) >= 2
    """, default=0)

    return jsonify({
        "cards": {
            "total": st.get("total",0),
            "em_transito": st.get("em_atencao",0),      # mapeado
            "manutencao":  st.get("em_atencao",0),
            "fase_final":  st.get("liberada_teste",0),
            "testes":      st.get("liberada_teste",0),
            "operacional": st.get("reparada",0),
            "entradas_hoje": today.get("entradas_hoje",0),
            "saidas_hoje":   today.get("saidas_hoje",0),
        },
        "time": {
            "mttr_dias": mttr or 0.0,
            "lead_time_30d": lead_30 or 0.0,
            "sla_proximas_48h": sla_48h or 0,
        },
        "trend": [dict(r) for r in trend],
        "aging_status": aging_map,
        "last_activities": [dict(r) for r in last],
    })

@app.route('/logout')
def logout(): 
    session.clear(); return redirect('/login')
# ======== ADMIN: tela de busca/ação em OS ========
@app.route('/admin/os')
@require_login
@require_roles('ADMIN')
def admin_os_page():
    return render_template('admin_os.html')

@app.get('/api/os')
@require_login
@require_roles('ADMIN')
def api_listar_os():
    # filtros: ?q=<texto os/equipamento> &status=Em%20atenção|Liberada%20para%20teste|Reparada|Não%20reparada
    q = (request.args.get('q') or '').strip()
    status = request.args.get('status') or ''
    sql = "SELECT id, os_numero, equipamento, status, data_registro, sla_inicio, pego_por_admin_em, liberado_teste_em, resultado_em FROM os WHERE 1=1"
    params = []
    if q:
        sql += " AND (os_numero LIKE ? OR equipamento LIKE ?)"
        like = f"%{q}%"; params += [like, like]
    if status:
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY data_registro DESC LIMIT 200"

    conn = get_conn(); cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(id=r['id'], os_numero=r['os_numero'], equipamento=r['equipamento'], status=r['status'],
                 data_registro=r['data_registro'], sla_inicio=r['sla_inicio'],
                 pego_por_admin_em=r['pego_por_admin_em'], liberado_teste_em=r['liberado_teste_em'],
                 resultado_em=r['resultado_em']) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)



if __name__ == "__main__":
    # garante que a pasta do banco existe
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # se você tiver funções migrate() e seed(), mantenha:
    try:
        migrate()
        seed()
    except Exception as e:
        print("Aviso: erro ao rodar migrate/seed ->", e)
    
    # inicializa o servidor
    app.run(host="0.0.0.0", port=5008, debug=True)


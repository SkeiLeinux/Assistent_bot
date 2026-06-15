import os, html, datetime, sys, re
ROOT = sys.argv[1]
TITLES = {
 "01-context":"Контекст системы (акторы и внешние системы)","02-context-map":"Карта bounded contexts и зависимости",
 "03-visibility":"Модель видимости (scope / hidden_from / подарки)","04-containers":"Контейнеры и компоненты (модульный монолит)",
 "05-feature-map":"Карта фич MVP","00-system":"Системный обзор (все акторы × домены)",
 "identity":"Identity & Sharing","lists":"Lists","finance":"Finance","nutrition":"Nutrition","reminders":"Reminders","analytics":"Analytics & Digest",
 "onboarding":"Онбординг и пейринг семьи","lists-item-lifecycle":"Жизненный цикл элемента списка","finance-import":"Импорт финансов",
 "nutrition-resolver":"Логирование питания по фото (resolver-ладдер)","reminder-lifecycle":"Жизненный цикл напоминания",
 "notification-delivery":"Доставка уведомления","digest":"Формирование и доставка дайджеста",
 "glossary":"Глоссарий","traceability":"Матрица трассировки",
 "prog-roadmap":"Программный roadmap E1→E5","prog-arch-e1":"Архитектура E1 (MVP)","prog-arch-e2e3":"Архитектура — дельта E2–E3",
 "prog-arch-e4e5":"Архитектура — дельта E4–E5","prog-feasibility":"Карта осуществимости интеграций",
 "stage-e2-finance":"E2 · Финансы-автоматизация — возможности","stage-e3-health":"E3 · Здоровье — возможности",
 "stage-e4-assistant":"E4 · Ассистентский слой — возможности","stage-e5-standalone":"E5 · Standalone — возможности",
 "e2-receipt":"BPMN E2 · Чек → транзакции (кандидат)","e2-ssot-transition":"BPMN E2 · Переход SSOT (кандидат)",
 "e3-huawei-import":"BPMN E3 · Huawei-импорт (кандидат)","e4-morning-plan":"BPMN E4 · Утренний план дня (кандидат)",
 "stage-pipeline":"BPMN · Конвейер открытия этапа",
}
ORDER = {
 "usecase":["00-system","identity","lists","finance","nutrition","reminders","analytics"],
 "bpmn":["onboarding","lists-item-lifecycle","finance-import","nutrition-resolver","reminder-lifecycle","notification-delivery","digest"],
 "reference":["glossary","traceability"],
 "program":["prog-roadmap","prog-arch-e1","prog-arch-e2e3","prog-arch-e4e5","prog-feasibility",
   "stage-e2-finance","e2-receipt","e2-ssot-transition","stage-e3-health","e3-huawei-import",
   "stage-e4-assistant","e4-morning-plan","stage-e5-standalone","stage-pipeline"],
}
SECTIONS = [("overview","1 · Обзор системы"),("usecase","2 · Use Case"),("bpmn","3 · Процессы (BPMN)"),
            ("program","4 · Программа E2–E5 (проектное)"),("reference","5 · Справочное")]
ALLOWED = {"overview":(".mermaid",),"usecase":(".mermaid",),"bpmn":(".bpmn",),"reference":(".md",),"program":(".mermaid",".bpmn")}
NOTE = {"program":"⚠️ Раздел проектный/кандидатный: этапы E2–E5 — каркас-видение, детальные ТЗ пишутся на входе этапа. "
        "Опросники Q2–Q4 ещё открыты, ADR-кандидаты 0006–0012 не приняты. Источник — docs/06_program. Диаграммы отражают план, не реализованное."}

def title_for(stem): return TITLES.get(stem, stem.replace("-"," ").replace("_"," ").capitalize())
def caption_of(text):
    for line in text.splitlines():
        s=line.strip()
        if s.startswith("%%") and "Derived" in s: return s.lstrip("% ").strip()
    return ""
def order_key(folder,stem):
    o=ORDER.get(folder)
    if o and stem in o: return (0,o.index(stem))
    return (1,stem)
def inline(s):
    s=html.escape(s); s=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',s); s=re.sub(r'`(.+?)`',r'<code>\1</code>',s); return s
def md2html(md):
    lines=md.split("\n"); out=[]; i=0
    while i<len(lines):
        ln=lines[i]
        if ln.startswith("### "): out.append(f"<h4>{inline(ln[4:])}</h4>"); i+=1; continue
        if ln.startswith("## "): out.append(f"<h3>{inline(ln[3:])}</h3>"); i+=1; continue
        if ln.strip().startswith("|") and i+1<len(lines) and set(lines[i+1].replace("|","").strip())<=set("-: "):
            header=[c.strip() for c in ln.strip().strip("|").split("|")]; i+=2; rows=[]
            while i<len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i+=1
            th="".join(f"<th>{inline(c)}</th>" for c in header)
            trs="".join("<tr>"+"".join(f"<td>{inline(c)}</td>" for c in r)+"</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"); continue
        if ln.strip().startswith("- "):
            items=[]
            while i<len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{inline(lines[i].strip()[2:])}</li>"); i+=1
            out.append("<ul>"+"".join(items)+"</ul>"); continue
        if ln.strip()=="": i+=1; continue
        out.append(f"<p>{inline(ln)}</p>"); i+=1
    return "\n".join(out)

nav=[]; body=[]; bpmn_scripts=[]
for folder,label in SECTIONS:
    d=os.path.join(ROOT,folder)
    if not os.path.isdir(d): continue
    allow=ALLOWED[folder]
    files=[f for f in os.listdir(d) if f.endswith(allow)]
    files.sort(key=lambda f: order_key(folder, f.rsplit(".",1)[0]))
    if not files: continue
    body.append(f'<h2 class="sec" id="sec-{folder}">{label}</h2>')
    if folder in NOTE: body.append(f'<div class="prognote">{NOTE[folder]}</div>')
    nav.append(f'<div class="navsec">{label}</div>')
    for f in files:
        stem=f.rsplit(".",1)[0]; fext="."+f.rsplit(".",1)[1]; anchor=f"{folder}-{stem}"; t=title_for(stem)
        nav.append(f'<a href="#{anchor}">{html.escape(t)}</a>')
        raw=open(os.path.join(d,f),encoding="utf-8").read()
        body.append(f'<section class="card" id="{anchor}"><h3>{html.escape(t)}</h3>')
        if fext==".mermaid":
            cap=caption_of(raw)
            if cap: body.append(f'<div class="cap">{html.escape(cap)}</div>')
            body.append(f'<pre class="mermaid">{html.escape(raw)}</pre>')
        elif fext==".bpmn":
            body.append('<div class="cap">BPMN 2.0 · производная от ТЗ/каркаса · редактируется в Camunda Modeler</div>')
            cid=f"bpmn-{stem}"
            body.append(f'<div class="bpmn-box"><div class="bpmn-canvas" id="{cid}"></div></div>')
            bpmn_scripts.append(f'<script type="application/bpmn" data-target="{cid}">{raw}</script>')
        else:
            body.append(md2html(raw))
        body.append('</section>')

today=datetime.date.today().isoformat()
HEAD = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Семейный ассистент — каталог диаграмм</title>
<style>
:root{--bg:#fff;--ink:#1a1a1a;--mut:#5f6368;--line:#e0e0e0;--accent:#4285F4;--side:#f7f8fa}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{display:flex;min-height:100vh}
aside{width:300px;flex:0 0 300px;background:var(--side);border-right:1px solid var(--line);padding:18px 14px;position:sticky;top:0;height:100vh;overflow:auto}
aside h1{font-size:15px;margin:0 0 4px}
aside .meta{font-size:12px;color:var(--mut);margin-bottom:14px;line-height:1.5}
.navsec{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin:14px 0 6px;font-weight:700}
aside a{display:block;font-size:13px;color:var(--ink);text-decoration:none;padding:5px 8px;border-radius:6px;line-height:1.35}
aside a:hover{background:#eceff3}
main{flex:1;padding:28px 36px;max-width:1120px}
h2.sec{font-size:20px;border-bottom:2px solid var(--accent);padding-bottom:6px;margin:34px 0 18px}
h2.sec:first-child{margin-top:0}
.card{border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:0 0 22px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.card h3{margin:0 0 4px;font-size:16px}
.card h4{margin:14px 0 6px;font-size:14px;color:#202124}
.cap{font-size:12px;color:var(--mut);margin-bottom:14px;font-family:ui-monospace,Menlo,Consolas,monospace}
pre.mermaid{margin:0;text-align:center;background:#fff;overflow:auto}
.bpmn-box{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
.bpmn-canvas{height:500px}
.card table{border-collapse:collapse;font-size:13px;margin:8px 0;width:100%}
.card th,.card td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
.card th{background:#f1f3f4}
.card ul{font-size:14px;line-height:1.65;margin:6px 0 6px 0;padding-left:20px}
.card code{background:#f1f3f4;padding:1px 5px;border-radius:4px;font-size:12px}
.banner{background:#fff;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:12px 16px;font-size:13px;color:var(--mut);margin-bottom:22px;line-height:1.6}
.prognote{background:#FEF7E0;border:1px solid #F9AB00;border-radius:8px;padding:12px 16px;font-size:13px;color:#5f4b00;margin:0 0 18px;line-height:1.55}
.legend{font-size:12px;color:var(--mut);margin-top:6px}
.err{color:#c5221f;padding:12px}
@media(max-width:820px){.wrap{flex-direction:column}aside{width:auto;height:auto;position:static}}
</style></head><body><div class="wrap">"""
aside = '<aside><h1>Семейный ассистент</h1><div class="meta">Каталог диаграмм · MVP + программа E2–E5<br>Дата: %s<br>Диаграммы — производные от ТЗ/каркасов.<br>SSOT: docs/04_specs, docs/03_architecture, docs/06_program</div>%s</aside>' % (today, "\n".join(nav))
banner = ('<div class="banner"><b>Как читать.</b> Обзор, Use Case и карты — Mermaid; процессы — BPMN 2.0 (рендер bpmn-js; зум колесом, панорама перетаскиванием). '
          'Разделы 1–3 и 5 — реализованный/принятый MVP (SSOT — <code>docs/04_specs</code>). Раздел 4 — программа E2–E5, проектное (SSOT — <code>docs/06_program</code>).'
          '<div class="legend">BPMN: иконка человека = действие Партнёра (userTask); шестерёнка = действие Системы (serviceTask); ромб = шлюз; круг = событие; круг с часами = таймер.</div></div>')
TAIL = """</main></div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script src="https://unpkg.com/bpmn-js@17.11.1/dist/bpmn-navigated-viewer.production.min.js"></script>
<script>
mermaid.initialize({startOnLoad:true,securityLevel:'loose',theme:'default',flowchart:{htmlLabels:true,useMaxWidth:true}});
window.addEventListener('load',function(){
  document.querySelectorAll('script[type="application/bpmn"]').forEach(function(s){
    var box=document.getElementById(s.dataset.target);
    try{ var v=new BpmnJS({container:box});
      v.importXML(s.textContent).then(function(){ v.get('canvas').zoom('fit-viewport','auto'); })
       .catch(function(e){box.innerHTML='<p class=err>BPMN error: '+e.message+'</p>';});
    }catch(e){box.innerHTML='<p class=err>BPMN init error: '+e.message+'</p>';}
  });
});
</script></body></html>"""
open(os.path.join(ROOT,"catalog.html"),"w",encoding="utf-8").write(HEAD+aside+'<main>'+banner+"\n".join(body)+"\n".join(bpmn_scripts)+TAIL)
print("catalog rebuilt")

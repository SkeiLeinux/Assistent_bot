import html, os
NS='xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'
def esc(s): return html.escape(s, quote=True)
def build(pid,pname,nodes,flows):
    inc={}; out={}
    for i,(s,t,*r) in enumerate(flows):
        fid=f"f{i+1}"; out.setdefault(s,[]).append(fid); inc.setdefault(t,[]).append(fid)
    el=[]
    for nid,kind,name in nodes:
        io="".join(f"<bpmn:incoming>{x}</bpmn:incoming>" for x in inc.get(nid,[]))+ "".join(f"<bpmn:outgoing>{x}</bpmn:outgoing>" for x in out.get(nid,[]))
        n=esc(name)
        tag,extra={"start":("startEvent",""),"start_timer":("startEvent","<bpmn:timerEventDefinition/>"),
           "end":("endEvent",""),"user":("userTask",""),"svc":("serviceTask",""),"xor":("exclusiveGateway","")}[kind]
        el.append(f'<bpmn:{tag} id="{nid}" name="{n}">{io}{extra}</bpmn:{tag}>')
    fl=[]
    for i,(s,t,*r) in enumerate(flows):
        nm=f' name="{esc(r[0])}"' if r else ""
        fl.append(f'<bpmn:sequenceFlow id="f{i+1}"{nm} sourceRef="{s}" targetRef="{t}"/>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<bpmn:definitions {NS} id="Defs_{pid}" targetNamespace="http://assistent-bot/bpmn">\n'
            f'<bpmn:process id="Proc_{pid}" name="{esc(pname)}" isExecutable="false">\n'+"\n".join(el)+"\n"+"\n".join(fl)+"\n</bpmn:process>\n</bpmn:definitions>\n")

P={}
P["e2-receipt"]=("E2 · Чек: от кассы до транзакций (кандидат)",[
 ("s","start","Купил, чек на руках"),("gway","xor","Способ?"),
 ("u3","user","Партнёр: сканирует QR (showScanQrPopup)"),("u4","user","Партнёр: фотографирует чек"),
 ("u5","user","Партнёр: ручной ввод — сумма + категория"),
 ("acl","svc","Receipt ACL: канал A/B (qrraw) / D (vision) → proverkacheka / API ФНС / Groq vision"),
 ("gchan","xor","Канал ответил?"),("raw","svc","Система: сохранить сырьё (Receipt.raw_ref), статус «к разбору»"),
 ("eLater","end","Отложено — сырьё сохранено (разбор позже)"),
 ("draft","svc","Система: ParsedReceipt → черновик позиций"),
 ("cat","svc","Система: категоризация (ADR-0011) — правила + LLM-подсказка"),
 ("u9","user","Партнёр: правит позиции/категории, подтверждает"),
 ("save","svc","Система: сохранить Receipt + ReceiptItems + Transactions"),
 ("an","svc","Система: аналитика E1 видит факт сразу (агрегат от Transaction)"),("eDone","end","Готово"),
],[("s","gway"),("gway","u3","QR"),("gway","u4","нет QR / не РФ"),("gway","u5","чека нет"),
 ("u3","acl"),("u4","acl"),("acl","gchan"),("gchan","raw","нет"),("gchan","draft","да"),
 ("raw","eLater"),("draft","cat"),("cat","u9"),("u9","save"),("u5","save"),("save","an"),("an","eDone")])

P["e2-ssot-transition"]=("E2 · Переход SSOT: таблица → транзакции (кандидат, ADR-0007)",[
 ("s","start","Старт E2"),("par","svc","Параллельный период: транзакции в боте + таблица как раньше"),
 ("g","xor","Сверка месяца: Σ транзакций ≈ таблица?"),
 ("fix","user","Партнёр: разбор причин (забытые траты? канал?) → правки привычек/категорий"),
 ("arch","svc","Система: таблица → архив; Import ACL → консервация (код остаётся, не зовётся)"),
 ("ritual","user","Партнёр (Катя): экспорт периода (xlsx) из бота (F2.6)"),("eGate","end","Выход-гейт E2"),
],[("s","par"),("par","g"),("g","fix","расхождения"),("g","arch","сходится N месяцев"),
 ("fix","par"),("arch","ritual"),("ritual","eGate")])

P["e3-huawei-import"]=("E3 · Huawei-импорт здоровья (кандидат)",[
 ("s","start","Напоминание-пресет: «запроси экспорт» (раз в 1–2 нед)"),
 ("h2","user","Партнёр: Huawei Health → Privacy center → Request data → Health"),
 ("h3","user","Партнёр: получает zip на почту, скачивает"),
 ("h4","user","Партнёр: грузит zip в Mini App (+ пароль архива)"),
 ("b1","svc","Health ACL: распаковка → парс JSON-папок (Health detail / Sport / Sleep…)"),
 ("b2","svc","Система: нормализация → Measurement / Workout"),("g","xor","Пересечение с ручными записями?"),
 ("dedup","svc","Система: дедупликация по ADR-0008 (источник-приоритет)"),
 ("up","svc","Система: идемпотентный upsert по периодам"),
 ("destroy","svc","Система: zip уничтожается (медданные не храним сырьём)"),
 ("h7","user","Партнёр: смотрит отчёт импорта (новые периоды, конфликты)"),("eDone","end","Готово"),
],[("s","h2"),("h2","h3"),("h3","h4"),("h4","b1"),("b1","b2"),("b2","g"),
 ("g","dedup","да"),("g","up","нет"),("dedup","up"),("up","destroy"),("destroy","h7"),("h7","eDone")])

P["e4-morning-plan"]=("E4 · Утренний план дня (кандидат)",[
 ("s","start_timer","job: morning_plan (per-user время, таймзона kernel)"),
 ("a1","svc","assistant: сбор — reminders сегодня · TASK-OPEN с близкими датами · тренировка (E3, если расшарена) · идея ужина"),
 ("a2","svc","assistant: LLM-композиция в текст (границы — ADR-0012)"),("g","xor","План пуст?"),
 ("skip","end","Не шлём"),("n1","svc","notifications: deliver(user, план, kind=morning_plan)"),
 ("u","user","Партнёр: читает план, тапает в Mini App"),("eDone","end","Готово"),
],[("s","a1"),("a1","a2"),("a2","g"),("g","skip","да"),("g","n1","нет"),("n1","u"),("u","eDone")])

P["stage-pipeline"]=("Конвейер открытия этапа (E2–E5)",[
 ("s","start","Гейт-триггер этапа"),("q","user","Опросник Q заполнен (С. + Катя)"),
 ("gt","user","Gate-тест feasibility (руками, без кода)"),("g","xor","Путь жив?"),
 ("rev","user","Ревизия stage-doc: смена канала / объёма"),
 ("adr","user","ADR этапа (варианты → решение С./Кати)"),
 ("spec","svc","spec-writer-agent: черновик ТЗ из stage-doc + Q + ADR"),
 ("accept","user","Ревью и принятие ТЗ (С./Катя)"),
 ("waves","svc","Волны агентов по build-contract (агенты → integrator → reviewer)"),
 ("exit","svc","Выход-гейт + ретро + ревизия следующего stage-doc"),("eDone","end","Этап закрыт"),
],[("s","q"),("q","gt"),("gt","g"),("g","rev","нет"),("g","adr","да"),("rev","q"),
 ("adr","spec"),("spec","accept"),("accept","waves"),("waves","exit"),("exit","eDone")])

for pid,(pname,nodes,flows) in P.items():
    open(f"{os.environ['DIAG']}/bpmn_src_program/{pid}.src.bpmn","w",encoding="utf-8").write(build(pid,pname,nodes,flows))
    print("gen",pid,len(nodes),"nodes")

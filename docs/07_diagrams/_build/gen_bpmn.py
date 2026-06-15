import html, os
NS='xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'
def esc(s): return html.escape(s, quote=True)
def build(pid, pname, nodes, flows):
    inc={}; out={}
    for i,(s,t,*r) in enumerate(flows):
        fid=f"f{i+1}"; out.setdefault(s,[]).append(fid); inc.setdefault(t,[]).append(fid)
    el=[]
    for nid,kind,name in nodes:
        io="".join(f"<bpmn:incoming>{x}</bpmn:incoming>" for x in inc.get(nid,[]))
        io+="".join(f"<bpmn:outgoing>{x}</bpmn:outgoing>" for x in out.get(nid,[]))
        n=esc(name)
        if kind=="start": el.append(f'<bpmn:startEvent id="{nid}" name="{n}">{io}</bpmn:startEvent>')
        elif kind=="start_timer": el.append(f'<bpmn:startEvent id="{nid}" name="{n}">{io}<bpmn:timerEventDefinition/></bpmn:startEvent>')
        elif kind=="end": el.append(f'<bpmn:endEvent id="{nid}" name="{n}">{io}</bpmn:endEvent>')
        elif kind=="user": el.append(f'<bpmn:userTask id="{nid}" name="{n}">{io}</bpmn:userTask>')
        elif kind=="svc": el.append(f'<bpmn:serviceTask id="{nid}" name="{n}">{io}</bpmn:serviceTask>')
        elif kind=="xor": el.append(f'<bpmn:exclusiveGateway id="{nid}" name="{n}">{io}</bpmn:exclusiveGateway>')
    fl=[]
    for i,(s,t,*r) in enumerate(flows):
        fid=f"f{i+1}"; nm=f' name="{esc(r[0])}"' if r else ""
        fl.append(f'<bpmn:sequenceFlow id="{fid}"{nm} sourceRef="{s}" targetRef="{t}"/>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<bpmn:definitions {NS} id="Defs_{pid}" targetNamespace="http://assistent-bot/bpmn">\n'
            f'<bpmn:process id="Proc_{pid}" name="{esc(pname)}" isExecutable="false">\n'+ "\n".join(el)+"\n"+"\n".join(fl)+"\n</bpmn:process>\n</bpmn:definitions>\n")

P={}
P["lists-item-lifecycle"]=("Жизненный цикл элемента списка",[
 ("s","start","Открыл список"),("t1","user","Партнёр: добавить элемент"),
 ("v1","svc","Система: валидировать иерархию и specific_attrs"),("g1","xor","Валидно?"),
 ("e422a","end","422 — вложение/атрибут"),("g2","xor","hidden_from задан? (подарок)"),
 ("v2","svc","Система: проверить hidden_from (FAMILY, не owner, член семьи)"),("g3","xor","Корректно?"),
 ("e422b","end","422 — нарушение видимости"),("sv","svc","Система: сохранить ListItem (OPEN) + история CREATED"),
 ("t2","user","Партнёр: отметить DONE"),("vis","svc","Система: visible_to(viewer)"),("g4","xor","Виден зрителю?"),
 ("e404","end","404 — скрыт от зрителя"),("done","svc","Система: OPEN→DONE + история STATUS_CHANGED"),
 ("g5","xor","Ещё действие?"),("edit","svc","Система: перенести/удалить (+история, каскад)"),
 ("eEdit","end","Элемент изменён"),("eDone","end","Готово (DONE)"),
],[("s","t1"),("t1","v1"),("v1","g1"),("g1","e422a","нет"),("g1","g2","да"),("g2","v2","да"),("g2","sv","нет"),
 ("v2","g3"),("g3","e422b","нет"),("g3","sv","да"),("sv","t2"),("t2","vis"),("vis","g4"),("g4","e404","нет"),
 ("g4","done","да"),("done","g5"),("g5","edit","перенести/удалить"),("g5","eDone","готово"),("edit","eEdit")])

P["finance-import"]=("Импорт финансов",[
 ("s","start","Готов импортировать"),("t1","user","Партнёр: загрузить xlsx (+ год)"),
 ("p1","svc","Import ACL: структурный парсинг (шапка, заливка)"),("g1","xor","Структура узнана?"),
 ("fb","svc","Import ACL: fallback — родитель = Σ детей"),("p2","svc","Import ACL: MonthParse + валидация (|Δ|≤1₽)"),
 ("g2","xor","Валидно?"),("eFail","end","Fail-loud — ничего не импортировано"),
 ("norm","svc","Import ACL: нормализация + алиасы"),("g3","xor","Категория известна?"),
 ("rev","svc","Система: категория NEEDS_REVIEW"),("up","svc","Система: идемпотентный upsert месяца"),
 ("eOk","end","Импортировано — аналитика доступна"),
],[("s","t1"),("t1","p1"),("p1","g1"),("g1","fb","нет"),("g1","p2","да"),("fb","p2"),("p2","g2"),
 ("g2","eFail","расхождение"),("g2","norm","ок"),("norm","g3"),("g3","rev","невиданная"),("g3","up","известная"),
 ("rev","up"),("up","eOk")])

P["nutrition-resolver"]=("Логирование питания по фото (resolver-ладдер)",[
 ("s","start","Текст / фото + порция"),("ext","svc","Groq Scout: извлечь позиции (1 LLM-вызов)"),
 ("g1","xor","label_complete?"),("off","svc","OpenFoodFacts: поиск (штрихкод / brand+name+fat)"),
 ("g3","xor","КБЖУ найдены и полны?"),("g4","xor","web-ступень включена? (R2)"),
 ("web","svc","Groq Compound: веб-поиск + цитата"),("g5","xor","Найдено?"),
 ("est","svc","EstimateSource: оценка (без LLM)"),("scale","svc","Система: масштаб на порцию + провенанс"),
 ("conf","user","Партнёр: confirm-экран (источник, правка)"),("g6","xor","Подтвердить?"),
 ("eCancel","end","Отменено"),("save","svc","Система: сохранить FoodEntry (+source)"),
 ("g7","xor","source≠estimate и есть семья?"),("shared","svc","Система: upsert SharedFood (кэш)"),("eDone","end","Готово"),
],[("s","ext"),("ext","g1"),("g1","scale","да: label"),("g1","off","нет"),("off","g3"),("g3","scale","да: off"),
 ("g3","g4","нет"),("g4","web","да (R2)"),("g4","est","нет (R1)"),("web","g5"),("g5","scale","да: web"),("g5","est","нет"),
 ("est","scale"),("scale","conf"),("conf","g6"),("g6","eCancel","отмена"),("g6","save","подтвердить"),
 ("save","g7"),("g7","shared","да"),("g7","eDone","нет"),("shared","eDone")])

P["reminder-lifecycle"]=("Жизненный цикл напоминания",[
 ("s","start","Партнёр создаёт напоминание (ACTIVE)"),("tick","svc","Планировщик: тик — выбрать созревшие"),
 ("g1","xor","Есть созревшие?"),("eWait","end","Нет — ждать след. тик"),
 ("claim","svc","Система: транзакционный claim (один push)"),("g2","xor","В окне misfire-грейса (3 ч)?"),
 ("deliver","svc","Система: last_fired_at=now; доставка через Notifications (push ✅/⏰)"),
 ("grec","xor","recurrence?"),("eDone","end","DONE (next_fire_at=NULL)"),
 ("adv","svc","Система: advance next_fire_at; статус ACTIVE"),("eReque","end","Снова в пуле (ACTIVE)"),
 ("gmiss","xor","recurrence? (просрочено сверх грейса)"),
 ("missF","svc","Система: продвинуть к ближайшему будущему слоту; ACTIVE"),("eReque2","end","Снова в пуле (ACTIVE)"),
 ("eMissed","end","MISSED (одноразовое)"),
 ("s2","start","Партнёр нажал кнопку в push"),("greact","xor","Реакция?"),
 ("ack","svc","Система: ack — одноразовое→DONE / повтор→подтвердить (расписание не трогает)"),("eAck","end","Подтверждено"),
 ("snooze","svc","Система: snooze — next_fire_at=now+N, ACTIVE"),("eSnooze","end","Отложено (ACTIVE)"),
],[("s","tick"),("tick","g1"),("g1","eWait","нет"),("g1","claim","да"),("claim","g2"),
 ("g2","deliver","в окне"),("g2","gmiss","сверх грейса"),("deliver","grec"),
 ("grec","eDone","none"),("grec","adv","daily/weekly/interval"),("adv","eReque"),
 ("gmiss","eMissed","none"),("gmiss","missF","daily/weekly/interval"),("missF","eReque2"),
 ("s2","greact"),("greact","ack","✅ ack"),("greact","snooze","⏰ snooze"),("ack","eAck"),("snooze","eSnooze")])

P["notification-delivery"]=("Доставка уведомления",[
 ("s","start","Источник: уведомление + получатели (visible_to)"),("deliver","svc","Notifications: deliver(recipients, text, buttons?)"),
 ("send","svc","Отправить в Telegram (best-effort)"),("g1","xor","Исход отправки?"),
 ("ok","svc","Учесть в sent"),("blocked","svc","Заблокирован/bad request: лог, без ретрая"),
 ("retry","svc","Транзиентный: один ретрай"),("g2","xor","После ретрая — ок?"),
 ("alert","svc","alert_admin → админ"),("g3","xor","Есть callback?"),
 ("route","svc","delivery/bot: callback → источник (ack/snooze)"),("eEnd","end","DeliveryReport {sent, blocked, failed}"),
],[("s","deliver"),("deliver","send"),("send","g1"),("g1","ok","успех"),("g1","blocked","блокировка"),("g1","retry","транзиентный"),
 ("retry","g2"),("g2","ok","успех"),("g2","alert","снова фейл"),("ok","g3"),("blocked","eEnd"),("alert","eEnd"),
 ("g3","route","да"),("g3","eEnd","нет"),("route","eEnd")])

P["digest"]=("Формирование и доставка дайджеста",[
 ("s","start_timer","Тик по каденсу DigestSchedule"),("g1","xor","Расписание созрело?"),
 ("eNo","end","Не шлётся (обзор в Mini App)"),("read","svc","Система: читать изменения (lists/finance/nutrition .read)"),
 ("vis","svc","Система: фильтр visible_to per-recipient"),("compose","svc","Система: композиция дайджеста"),
 ("g2","xor","Непустой дайджест?"),("eEmpty","end","Пусто — не отправляется"),
 ("push","svc","Notifications → push каждому в семье"),("upd","svc","Система: last_sent_at = now"),("eDone","end","Дайджест доставлен"),
],[("s","g1"),("g1","eNo","нет/disabled"),("g1","read","созрело"),("read","vis"),("vis","compose"),("compose","g2"),
 ("g2","eEmpty","пусто"),("g2","push","есть контент"),("push","upd"),("upd","eDone")])

os.makedirs(f"{DIAG}/bpmn_src",exist_ok=True) if False else None
for pid,(pname,nodes,flows) in P.items():
    open(f"{os.environ['DIAG']}/bpmn_src/{pid}.src.bpmn","w",encoding="utf-8").write(build(pid,pname,nodes,flows))
    print("gen", pid, len(nodes),"nodes",len(flows),"flows")

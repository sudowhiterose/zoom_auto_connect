import re
import time
from playwright.sync_api import sync_playwright

YOUR_NAME = "Пищиков Всеволод"

CONFERENCES = [
    {"id": "3447205318", "password": "123"},
    {"id": "2117906638", "password": "123"},
]

# Если встреча не началась / вы в зале ожидания — сколько ждать максимум (сек)
MAX_WAIT_JOIN = 10 * 60
# Максимальная длительность одной конференции (сек), страховка от зависания
MAX_MEETING_TIME = 6 * 60 * 60
# Как часто проверять, не закончилась ли конференция (сек)
POLL = 5

# Фразы, по которым понимаем, что конференция закончилась / нас выкинуло
END_PATTERNS = re.compile(
    r"(meeting has been ended|meeting has ended|host has ended|"
    r"you have been removed|removed you from|this meeting has ended|"
    r"конференция.*завершена|собрание.*завершено|встреча.*завершена|"
    r"организатор.*завершил|вас удалили|invalid meeting id|недействительный)",
    re.I,
)

# Признаки того, что мы уже внутри встречи
IN_MEETING_SELECTORS = [
    "button[aria-label*='Leave' i]",
    "button[aria-label*='Выйти' i]",
    "button:has-text('Leave')",
    "button:has-text('Покинуть')",
    "#foot-bar",
    ".footer__leave-btn",
]

# Кнопки, которые нужно нажимать «по дороге» (первая найденная за проход)
CLICK_BUTTONS = [
    "#onetrust-accept-btn-handler",                       # cookies
    "button:has-text('Accept Cookies')",
    "button:has-text('Принять')",
    "button:has-text('Join from Your Browser')",          # выбор «через браузер»
    "a:has-text('Join from Your Browser')",
    "a:has-text('Войдите через браузер')",
    "a:has-text('Присоединиться из браузера')",
    "button:has-text('Continue without microphone and camera')",
    "button:has-text('Продолжить без микрофона и камеры')",
    "button:has-text('Продолжить без микрофона')",
    "button:has-text('Continue without')",
    "button:has-text('Got it')",
    "button:has-text('Понятно')",
    "button:has-text('Agree')",
    "button:has-text('I Agree')",
    "button:has-text('Согласен')",
    "button:has-text('Принимаю')",
    "button:has-text('Join Audio by Computer')",          # аудио нам не нужно
]
# Эти кнопки можно закрыть, а не принимать:
CLOSE_BUTTONS = [
    "button:has-text('Not now')",
    "button:has-text('Не сейчас')",
    "button:has-text('Close')",
    "button:has-text('Закрыть')",
]


def all_frames(page):
    return [page] + [f for f in page.frames if f != page.main_frame]


def try_click(page, selectors):
    """Кликает первую найденную видимую кнопку в странице или фреймах."""
    for frame in all_frames(page):
        for sel in selectors:
            try:
                loc = frame.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=2000)
                    print(f"    [click] {sel}")
                    return True
            except Exception:
                pass
    return False


def fill_form(page, password):
    """Заполняет имя и код, если поля видны, и жмёт Join."""
    for frame in all_frames(page):
        try:
            name = frame.locator("#input-for-name, input[placeholder*='name' i], "
                                 "input[placeholder*='имя' i]").first
            if name.count() and name.is_visible():
                if name.input_value() != YOUR_NAME:
                    name.fill(YOUR_NAME)
                    print("    [fill] имя")

                pwd = frame.locator("#input-for-pwd, input[type='password']").first
                if pwd.count() and pwd.is_visible() and not pwd.input_value():
                    pwd.fill(password)
                    print("    [fill] код доступа")

                join = frame.locator(
                    "button.preview-join-button, button:has-text('Join'), "
                    "button:has-text('Войти'), button:has-text('Присоединиться')"
                ).first
                if join.count() and join.is_enabled():
                    join.click(timeout=2000)
                    print("    [click] Join")
                    return True
        except Exception:
            pass
    return False


def in_meeting(page):
    for frame in all_frames(page):
        for sel in IN_MEETING_SELECTORS:
            try:
                if frame.locator(sel).first.is_visible():
                    return True
            except Exception:
                pass
    return False


def meeting_ended(page):
    try:
        if page.is_closed():
            return True
        if re.search(r"/(postattendee|wc/leave|wc/\w+/end)", page.url):
            return True
        for frame in all_frames(page):
            try:
                text = frame.locator("body").inner_text(timeout=1500)
            except Exception:
                continue
            if END_PATTERNS.search(text):
                return True
    except Exception:
        return True
    return False


def run_conference(p, conf):
    cid = conf["id"].replace(" ", "")
    # /wc/join/ — сразу веб-клиент, без страницы «открыть приложение»
    url = f"https://zoom.us/wc/join/{cid}?pwd={conf['password']}"
    print(f"\n[+] Подключаюсь: {cid}")

    browser = p.firefox.launch(
        headless=False,
        firefox_user_prefs={
            # 2 = всегда блокировать (без всплывающего запроса)
            "permissions.default.microphone": 2,
            "permissions.default.camera": 2,
            "permissions.default.desktop-notification": 2,
            "permissions.default.geo": 2,
            "media.navigator.permission.disabled": False,
            # не предлагать открыть zoommtg:// во внешнем приложении
            "network.protocol-handler.external.zoommtg": False,
            "network.protocol-handler.external.zoomus": False,
            "network.protocol-handler.warn-external.zoommtg": False,
        },
    )
    try:
        context = browser.new_context(permissions=[])
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # ---- Фаза 1: пройти все экраны до входа во встречу ----
        start = time.time()
        joined = False
        last_submit = 0
        while time.time() - start < MAX_WAIT_JOIN:
            if meeting_ended(page):
                print("[-] Встреча недоступна/закончилась до входа.")
                return
            if in_meeting(page):
                joined = True
                break

            # форму имени/кода заполняем не чаще раза в 3 сек
            if time.time() - last_submit > 3 and fill_form(page, conf["password"]):
                last_submit = time.time()
            else:
                if not try_click(page, CLICK_BUTTONS):
                    try_click(page, CLOSE_BUTTONS)
            time.sleep(1)

        if not joined:
            print("[-] Не удалось войти за отведённое время.")
            return
        print("[+] Вошёл во встречу. Жду её окончания...")

        # ---- Фаза 2: ждём окончания ----
        t0 = time.time()
        while time.time() - t0 < MAX_MEETING_TIME:
            try_click(page, CLICK_BUTTONS)  # вдруг снова всплывёт окно про аудио
            if meeting_ended(page):
                print("[+] Конференция закончилась.")
                return
            time.sleep(POLL)
        print("[!] Достигнут лимит времени, выхожу.")
    finally:
        try:
            browser.close()
        except Exception:
            pass


def main():
    with sync_playwright() as p:
        while True:                      # бесконечный цикл по списку
            for conf in CONFERENCES:
                try:
                    run_conference(p, conf)
                except Exception as e:
                    print(f"[-] Ошибка ({conf['id']}): {e}")
                time.sleep(3)


if __name__ == "__main__":
    main()
from __future__ import annotations

"""
Fetch transaction and holdings files from bank/credit portals via Selenium.

Pipeline position: writes into ``config.download_inbox_dir`` (browser download folder).
Next step: ``inbox_router.route_shared_download_inbox`` then ``spreadsheet_ingest`` into each pipeline's ``raw`` dir.
"""
import logging
import urllib.parse
from datetime import datetime

import config
from selenium import webdriver, common
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Keys, ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import time

log = logging.getLogger(__name__)

project_dir = os.path.abspath(os.path.dirname(__file__))
download_dir = os.path.join(project_dir, config.download_inbox_dir)
log.debug("Portal download directory resolved to %s", download_dir)

# Set FINANCE_SELENIUM_DEBUG=1 to print optional-click outcomes and pause hints.
# Set FINANCE_SELENIUM_PAUSE=1 to wait for Enter after each flow_debug_pause(...) call.


def _selenium_debug() -> bool:
    return os.environ.get("FINANCE_SELENIUM_DEBUG", "").strip() in ("1", "true", "yes")


def _flow_debug_log(message: str) -> None:
    if _selenium_debug():
        log.debug("[selenium] %s", message)


def flow_debug_pause(_driver, label: str) -> None:
    """Block until Enter when FINANCE_SELENIUM_PAUSE is set (step-through debugging)."""
    if not os.environ.get("FINANCE_SELENIUM_PAUSE", "").strip() in ("1", "true", "yes"):
        return
    input(f"[selenium pause] {label}\nPress Enter to continue… ")


def optional_click(driver, locator, timeout: float = 5.0, description: str = "") -> bool:
    """
    Click if the element becomes clickable within timeout; otherwise return False.
    Does not raise — use for cookie banners, one-off overlays, etc.
    """
    by, value = locator
    label = description or f"{by}={value!r}"
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        el.click()
        log.info("SELENIUM optional ok: %s", label)
        _flow_debug_log(f"optional click OK: {label}")
        return True
    except TimeoutException:
        log.info("SELENIUM optional skip: %s (not clickable within %ss)", label, timeout)
        _flow_debug_log(f"optional click skipped (not found in {timeout}s): {label}")
        return False


def wait_click(driver, locator, timeout: float, description: str):
    """Wait until clickable, scroll into view, click; log and re-raise on timeout."""
    by, value = locator
    log.info("SELENIUM → %s", description)
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        el.click()
        log.info("SELENIUM ok: %s", description)
        return el
    except TimeoutException as e:
        log.error("SELENIUM TIMEOUT: %s (%s=%r)", description, by, value)
        raise TimeoutException(f"{description}: element not clickable within {timeout}s") from e


def timestamp_latest_file_in_dir(directory, file_extension=".xlsx"):
    """
    Renames the latest file in the specified directory with a timestamp.

    Parameters:
    directory (str): The directory where the file is located.
    file_extension (str): The extension of the file to rename (default is .xlsx).
    """
    # Short delay to ensure the file is completely written to disk
    time.sleep(2)

    # Find the latest file in the directory
    files = [f for f in os.listdir(directory) if f.endswith(file_extension)]
    if files:
        latest_file = max([os.path.join(directory, f) for f in files], key=os.path.getctime)
        current_time = datetime.now().strftime('%d-%m-%Y')
        new_filename = f"{os.path.splitext(latest_file)[0]}_{current_time}{os.path.splitext(latest_file)[1]}"
        os.rename(latest_file, new_filename)
        log.info("Renamed latest download to timestamped name: %s", new_filename)
    else:
        log.warning("timestamp_latest_file_in_dir: no %s files in %s", file_extension, directory)


def load_driver():
    log.info("Starting Chrome WebDriver (download dir=%s)", download_dir)
    os.makedirs(download_dir, exist_ok=True)

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    })

    driver = webdriver.Chrome(options=chrome_options)
    driver.maximize_window()
    log.debug("Chrome WebDriver ready")
    return driver


class Bank:
    def __init__(self, username, password):
        log.info("Bank portal session: initializing (Leumi)")
        self.__username = username
        self.__password = password
        self.__driver = load_driver()
        self.__driver = self.__login__()
        log.info("Bank portal session: login flow completed")

    def __login__(self):
        url = 'https://hb2.bankleumi.co.il/H/Login.html'
        log.info("SELENIUM: Leumi — open login URL")
        self.__driver.get(url)
        optional_click(
            self.__driver,
            (By.CSS_SELECTOR, "button.app-close-cookies-btn"),
            timeout=5,
            description="Leumi login — cookie banner close",
        )
        flow_debug_pause(self.__driver, "Leumi login page loaded (after cookie dismiss attempt)")
        time.sleep(2)
        log.info("SELENIUM: Leumi — enter credentials")
        username = self.__driver.find_element(By.NAME, "user")
        password = self.__driver.find_element(By.NAME, "password")
        time.sleep(1)
        username.send_keys(self.__username)
        time.sleep(1)
        password.send_keys(self.__password)
        wait_click(
            self.__driver,
            (By.XPATH, "//button[@type='submit' and contains(@class, 'cursor-pointer')]"),
            10,
            "Leumi — submit login",
        )
        WebDriverWait(self.__driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//span[contains(@class, 'welcome-text')]"))
        )
        log.info("SELENIUM: Leumi — welcome screen (login OK)")
        return self.__driver

    def download(self, file: str, from_date=None, to_date=None):
        log.info(
            "Bank.download requested: kind=%r from_date=%r to_date=%r",
            file,
            from_date,
            to_date,
        )
        if file.lower() == "holdings":
            log.info("Bank: holdings — open balances page")
            self.__driver.get("https://hb2.bankleumi.co.il/ebanking/Accounts/DisplayBalances.aspx#/")
            wait_click(self.__driver, (By.ID, "BTNEXCEL"), 10, "Leumi holdings — BTNEXCEL (open export popup)")
            WebDriverWait(self.__driver, 10).until(EC.number_of_windows_to_be(2))
            self.__driver.switch_to.window(self.__driver.window_handles[1])
            log.info("Bank: holdings — switched to export popup window")
            baseline = set(os.listdir(download_dir))
            log.info("Bank: holdings — baseline download dir (%s file(s))", len(baseline))
            wait_click(self.__driver, (By.ID, "ImgContinue"), 10, "Leumi holdings — ImgContinue (start download)")
            ok, new_name = verify_download(download_dir, poll_interval=1.5, timeout=120, baseline_names=baseline)
            if not ok:
                log.error("Bank: holdings — verify_download failed")
                raise FileNotFoundError("Failed to download holdings export.")
            log.info("Bank: holdings — saved as %r", new_name)
            return True
        elif file.lower() == "osh":
            log.info("Bank: osh — switch to main window and open account transactions")
            self.__driver.switch_to.window(self.__driver.window_handles[0])
            self.__driver.get(
                "https://hb2.bankleumi.co.il/ebanking/SO/SPA.aspx#/ts/BusinessAccountTrx?WidgetPar=1"
            )
            wait_click(
                self.__driver,
                (By.XPATH, "//button[@title='חיפוש מתקדם']"),
                10,
                "Leumi osh — advanced search",
            )
            wait_click(
                self.__driver,
                (By.XPATH, "//span[text()='תקופה']"),
                10,
                "Leumi osh — period",
            )

            date_start_input = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='מתאריך']"))
            )

            date_start_input.click()
            date_start_input.send_keys(Keys.CONTROL + "a")
            time.sleep(0.5)
            date_start_input.send_keys(Keys.DELETE)
            time.sleep(0.5)
            if from_date is None:
                current_year = str(datetime.now().year)[-2:]
                date_str = "01.01." + current_year
                log.debug("Osh export: defaulting start date to %s", date_str)
            else:
                date_str = from_date
            time.sleep(0.5)
            date_start_input.send_keys(date_str)
            date_start_input.click()
            time.sleep(0.5)
            if to_date is not None:
                date_end_input = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='עד תאריך']"))
                )
                date_end_input.click()
                date_end_input.send_keys(Keys.CONTROL + "a")
                date_end_input.send_keys(Keys.DELETE)
                date_end_input.send_keys(to_date)

            wait_click(
                self.__driver,
                (By.XPATH, "//button[@aria-label='סנן']"),
                10,
                "Leumi osh — apply filter",
            )
            log.info("Bank: osh — waiting for transaction grid")
            time.sleep(5)
            baseline = set(os.listdir(download_dir))
            log.info("Bank: osh — baseline download dir (%s file(s)); starting Excel export", len(baseline))
            wait_click(
                self.__driver,
                (By.XPATH, "//button[@title='יצוא לאקסל']"),
                10,
                "Leumi osh — export to Excel",
            )
            modal_dialog = WebDriverWait(self.__driver, 10).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "modal-dialog"))
            )
            buttons = modal_dialog.find_elements(By.TAG_NAME, "button")
            continue_btn = None
            for button in buttons:
                if "המשך" in button.text:
                    continue_btn = button
                    break
            if not continue_btn:
                log.error("Bank: osh — modal has no המשך button")
                raise FileNotFoundError("Osh Excel export: continue button not found.")
            log.info("Bank: osh — confirm export in modal")
            ActionChains(self.__driver).move_to_element(continue_btn).click().perform()
            ok, new_name = verify_download(download_dir, poll_interval=1.5, timeout=120, baseline_names=baseline)
            if not ok:
                log.error("Bank: osh — verify_download failed")
                raise FileNotFoundError("Failed to download osh export.")
            log.info("Bank: osh — saved as %r", new_name)
            return True
        elif file.lower() == "credit":
            log.info("Bank: credit — Leumi cards world (Isracard/Max popups)")
            self.__driver.get("https://hb2.bankleumi.co.il/ebanking/SO/SPA.aspx#/ts/CardsWorld")
            WebDriverWait(self.__driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.swiper-wrapper"))
            )
            cards = self.__driver.find_elements(
                By.CSS_SELECTOR,
                "div.swiper-slide:not(.blank) article.credit-card-item",
            )
            for index, card in enumerate(cards):
                amount_text = card.find_element(By.CSS_SELECTOR, "span.ng-star-inserted").text
                if amount_text == '':
                    log.debug("Credit card slide %s: skipping (empty balance text)", index)
                    continue
                if float(amount_text.replace(',', '')) <= 0:
                    log.debug("Credit card slide %s: skipping (non-positive balance)", index)
                    continue
                self.__driver.execute_script("arguments[0].scrollIntoView();", card)
                WebDriverWait(self.__driver, 10).until(EC.element_to_be_clickable(card)).click()

                details_list = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.ts-btn.dropdown-toggle'))
                )
                details_list.click()

                details_button = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//ul[@class='dropdown-menu']/li[1]/div/a/span[text()='דפי פירוט']"))
                )
                details_button.click()

                WebDriverWait(self.__driver, 10).until(EC.new_window_is_opened(self.__driver.window_handles))

                new_window = self.__driver.window_handles[-1]
                self.__driver.switch_to.window(new_window)
                current_url = self.__driver.current_url
                domain = urllib.parse.urlparse(current_url).netloc
                log.info("Bank: credit — card %s popup domain=%s", index, domain)
                if "isracard.co.il" in domain:
                    for attempt in range(15):
                        wait_click(
                            self.__driver,
                            (
                                By.CSS_SELECTOR,
                                "span[aria-label='בחר מועד חיוב מועד קרוב']",
                            ),
                            10,
                            "Isracard (Leumi) — billing period",
                        )
                        wait_click(
                            self.__driver,
                            (By.XPATH, "//div[@id='ui-select-choices-row-3-1']"),
                            10,
                            "Isracard (Leumi) — select billing option",
                        )
                        bl = set(os.listdir(download_dir))
                        wait_click(
                            self.__driver,
                            (
                                By.XPATH,
                                "//button[@aria-label='Excel הורד פירוט חיובים בפורמט']",
                            ),
                            10,
                            "Isracard (Leumi) — Excel download",
                        )
                        ok_is, _ = verify_download(
                            download_dir, poll_interval=1.5, timeout=90, baseline_names=bl
                        )
                        if ok_is:
                            self.__driver.close()
                            self.__driver.switch_to.window(self.__driver.window_handles[0])
                            break
                        log.warning(
                            "Bank: credit — Isracard download not ready (attempt %s/15)",
                            attempt + 1,
                        )
                        time.sleep(2)
                    else:
                        log.error("Bank: credit — Isracard popup: giving up after 15 attempts")
                        self.__driver.close()
                        self.__driver.switch_to.window(self.__driver.window_handles[0])
                        raise FileNotFoundError(
                            "Isracard (Leumi popup): download did not complete after retries."
                        )
                if "max.co.il" in domain:
                    for attempt in range(15):
                        months_hebrew = [
                            "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
                            "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
                        ]
                        current_date = datetime.now()
                        # Calendar month name is 1..12; array is 0..11 (was using month as index → off by one).
                        target_month_label = (
                            f"{months_hebrew[current_date.month - 1]} {current_date.year}"
                        )
                        log.info(
                            "Max (Leumi popup): billing month target=%r",
                            target_month_label,
                        )
                        wait_click(
                            self.__driver,
                            (
                                By.CSS_SELECTOR,
                                "div.combo.dates div.open-menu[role='button']",
                            ),
                            10,
                            "Max (Leumi) — open date combo",
                        )
                        wait_click(
                            self.__driver,
                            (
                                By.XPATH,
                                "//ul[contains(@class,'month-wrapper')]"
                                "//li[contains(@class,'month')]"
                                f"[normalize-space()='{target_month_label}']",
                            ),
                            10,
                            "Max (Leumi) — pick month",
                        )
                        blm = set(os.listdir(download_dir))
                        wait_click(
                            self.__driver,
                            (
                                By.XPATH,
                                "//a[contains(text(),'להורדת פירוט החיובים כקובץ אקסל')]",
                            ),
                            10,
                            "Max (Leumi) — Excel link",
                        )
                        ok_mx, _ = verify_download(
                            download_dir, poll_interval=1.5, timeout=90, baseline_names=blm
                        )
                        if ok_mx:
                            self.__driver.close()
                            self.__driver.switch_to.window(self.__driver.window_handles[0])
                            break
                        log.warning(
                            "Bank: credit — Max download not ready (attempt %s/15)",
                            attempt + 1,
                        )
                        time.sleep(2)
                    else:
                        log.error("Bank: credit — Max popup: giving up after 15 attempts")
                        self.__driver.close()
                        self.__driver.switch_to.window(self.__driver.window_handles[0])
                        raise FileNotFoundError(
                            "Max (Leumi popup): download did not complete after retries."
                        )
            log.info("Bank: credit — finished card iteration")
            return True
        raise ValueError(f"Unknown Bank.download kind {file!r} (use holdings, osh, or credit).")

    def __del__(self):
        log.debug("Bank portal session: closing WebDriver")
        self.__driver.quit()


class IsracardCredit:
    def __init__(self, username, password, last6):
        log.info("Isracard portal session: initializing")
        self.__username = username
        self.__last6 = last6
        self.__password = password
        self.__driver = load_driver()
        self.__driver = self.__login()
        log.info("Isracard portal session: login completed")

    def __login(self):
        log.debug("Isracard: opening login page")
        self.__driver.get('https://digital.isracard.co.il/personalarea/Login/')
        time.sleep(2)

        flip_form = WebDriverWait(self.__driver, 10).until(
            EC.presence_of_element_located((By.ID, "flip"))
        )
        flip_form.click()
        time.sleep(1)
        username = self.__driver.find_element(By.NAME, 'otpLoginId_ID')
        last_digit = self.__driver.find_element(By.NAME, 'otpLoginLastDigits_ID')
        password = self.__driver.find_element(By.NAME, 'otpLoginPwd')
        username.send_keys(self.__username)
        time.sleep(1)
        last_digit.send_keys(self.__last6)
        time.sleep(1)
        password.send_keys(self.__password)
        time.sleep(1)
        submit_button = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and @aria-label='כניסה לחשבון שלי']"))
        )
        submit_button.click()

        WebDriverWait(self.__driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//h1[contains(@class, 'page-title')]"))
        )
        log.debug("Isracard: page title visible after login")
        return self.__driver

    def download(self):
        log.info("Isracard.download: starting Excel export flow")
        WebDriverWait(self.__driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//h1[contains(@class, 'page-title')]"))
        )
        link = WebDriverWait(self.__driver, 15).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div[title='בחר כרטיס'] span.ui-select-toggle"))
        )
        link.click()
        link = WebDriverWait(self.__driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 f"//div[contains(@class, 'ui-select-choices-row')]//span[contains(text(), '{self.__last6[-4:]}')]"))
        )
        link.click()
        link = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@ng-click, \"dc._ga_pushBillingDates('PreviousBilling')\")]"))
        )
        link.click()

        link = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(@ng-click, \"dc.callExport('Excel', selectedDate2)\")]"))
        )
        time.sleep(3)
        baseline = set(os.listdir(download_dir))
        log.info("Isracard.download: baseline %s file(s); triggering Excel export", len(baseline))
        link.click()
        ok, new_file = verify_download(
            download_dir, poll_interval=1.5, timeout=120, baseline_names=baseline
        )
        if ok:
            log.info(
                "Isracard.download: success file=%r path=%s",
                new_file,
                os.path.join(download_dir, new_file) if new_file else None,
            )
        else:
            log.error("Isracard.download: verify_download failed (no new stable spreadsheet)")
            raise FileNotFoundError("Failed to download Isracard Excel export.")
        return True

    def __del__(self):
        log.debug("Isracard portal session: closing WebDriver")
        self.__driver.quit()


class MaxCredit:
    def __init__(self, username, password):
        log.info("Max portal session: initializing")
        self.username = username
        self.password = password
        self.__driver = load_driver()
        self.__driver = self.__login()
        log.info("Max portal session: login completed")

    def __login(self):
        log.debug("Max: opening homepage")
        self.__driver.get('https://www.max.co.il/')
        link = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@class, 'go-to-personal-area') and contains(@class, 'log-in-status')]"))
        )
        link.click()
        time.sleep(1)
        # Wait for the modal dialog to appear
        modal_dialog = WebDriverWait(self.__driver, 10).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "modal-dialog"))
        )
        time.sleep(1)
        # Click the "כניסה עם סיסמה" tab
        password_tab = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@id='login-password-link' and contains(@class, 'nav-link')]"))
        )
        time.sleep(1)
        password_tab.click()

        # Locate the username and password input fields
        username_input = WebDriverWait(self.__driver, 10).until(
            EC.presence_of_element_located((By.ID, "user-name"))
        )

        password_input = WebDriverWait(self.__driver, 10).until(
            EC.presence_of_element_located((By.ID, "password"))
        )

        # Input the username and password
        username_input.send_keys(self.username)  # Replace with your actual username
        time.sleep(1)
        password_input.send_keys(self.password)  # Replace with your actual password
        time.sleep(1)

        # Locate the submit button and click it
        submit_button = WebDriverWait(self.__driver, 25).until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='לכניסה לאזור האישי']/.."))
        )
        submit_button.click()

        WebDriverWait(self.__driver, 120).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'go-to-personal-area')]"))
        )
        log.debug("Max: personal area marker visible")
        return self.__driver

    def download(self, card_digits=None):
        log.info("Max.download: starting card_digits=%r", card_digits)
        self.__driver.get("https://www.max.co.il/transaction-details/personal")
        time.sleep(2)
        element = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'כל הכרטיסים')]"))
        )
        element.click()
        time.sleep(1)
        card_digits_list = []
        cards = self.__driver.find_elements(By.CLASS_NAME, 'card-item')

        for card in cards:
            text = card.text
            last_four_digits = text.split()[0]
            card_digits_list.append(last_four_digits)
        log.debug("Max.download: discovered card last-four list=%s", card_digits_list)
        if not card_digits:
            for card in card_digits_list:
                time.sleep(1)
                element = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, f"//li[contains(text(), '{card}')]")
                    )
                )
                element.click()
                time.sleep(1)
                element = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'ייצא לאקסל')]"))
                )
                time.sleep(2)
                bl = set(os.listdir(download_dir))
                log.info("Max.download: card=%r baseline %s file(s); export", card, len(bl))
                element.click()
                ok, new_file = verify_download(
                    download_dir, poll_interval=1.5, timeout=120, baseline_names=bl
                )
                if ok:
                    log.info(
                        "Max.download: card=%r saved as %r",
                        card,
                        new_file,
                    )
                else:
                    log.error("Max.download: verify failed for card=%r", card)
                    raise FileNotFoundError(f"Failed to download Max export for card {card!r}.")

                timestamp_latest_file_in_dir(download_dir, ".xlsx")

                element = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{card}')]"))
                )
                element.click()
        elif str(card_digits) in card_digits_list:
            card = str(card_digits)
            time.sleep(1)
            element = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, f"//li[contains(text(), '{card}')]")
                )
            )
            element.click()
            time.sleep(1)
            element = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'ייצא לאקסל')]"))
            )
            bl_one = set(os.listdir(download_dir))
            log.info("Max.download: single card=%r baseline %s file(s); export", card, len(bl_one))
            element.click()
            ok, new_file = verify_download(
                download_dir, poll_interval=1.5, timeout=90, baseline_names=bl_one
            )
            if ok:
                log.info("Max.download: single card=%r file=%r", card, new_file)
            else:
                log.error("Max.download: verify failed for card=%r", card)
                raise FileNotFoundError(f"Failed to download Max export for card {card!r}.")

            timestamp_latest_file_in_dir(download_dir, ".xlsx")
            time.sleep(5)
        else:
            log.error("Max.download: card_digits %r not in %s", card_digits, card_digits_list)
            raise Exception(f"Card digits {card_digits} not found within list inside website")

    def __del__(self):
        log.debug("Max portal session: closing WebDriver")
        self.__driver.quit()


def _is_stable_file(path: str, settle_seconds: float = 0.35) -> bool:
    """True if path is a regular file with non-zero size stable over a short window."""
    try:
        if not os.path.isfile(path):
            return False
        s1 = os.path.getsize(path)
        if s1 <= 0:
            return False
        time.sleep(settle_seconds)
        s2 = os.path.getsize(path)
        return s1 == s2
    except OSError:
        return False


def verify_download(
    download_dir: str,
    poll_interval: float = 1.5,
    timeout: float = 120.0,
    *,
    baseline_names: set[str] | None = None,
    extensions: tuple[str, ...] = (".xlsx", ".xls", ".xlsm", ".csv"),
) -> tuple[bool, str | None]:
    """
    Wait for a **new** spreadsheet (vs ``baseline_names``) to appear and stabilize.

    Ignores Chrome ``.crdownload`` placeholders. Replaces the old logic that compared
    file age to ``check_interval``, which rejected normal downloads.

    Args:
        download_dir: Browser download directory.
        poll_interval: Seconds between scans.
        timeout: Max seconds to wait.
        baseline_names: Filenames present *before* the download was triggered; if None,
            captured at call time.
        extensions: Accept only these suffixes (case-insensitive).

    Returns:
        (True, basename) on success, (False, None) on timeout.
    """
    os.makedirs(download_dir, exist_ok=True)
    if baseline_names is None:
        baseline_names = set(os.listdir(download_dir))
    else:
        baseline_names = set(baseline_names)

    t0 = time.time()
    deadline = t0 + timeout
    last_progress_log = t0
    poll_n = 0
    ext_l = tuple(e.lower() for e in extensions)

    log.info(
        "verify_download: watching %s (timeout=%ss, baseline=%s file(s))",
        download_dir,
        int(timeout),
        len(baseline_names),
    )

    while time.time() < deadline:
        poll_n += 1
        now = time.time()
        if now - last_progress_log >= 10.0:
            log.info(
                "verify_download: still waiting… %ds / %ds",
                int(now - t0),
                int(timeout),
            )
            last_progress_log = now

        try:
            names = os.listdir(download_dir)
        except OSError as e:
            log.warning("verify_download: listdir failed: %s", e)
            time.sleep(poll_interval)
            continue

        candidates: list[str] = []
        for n in names:
            if n in baseline_names:
                continue
            if n.endswith(".crdownload"):
                log.debug("verify_download: chrome still writing %r", n)
                continue
            nl = n.lower()
            if ext_l and not nl.endswith(ext_l):
                continue
            path = os.path.join(download_dir, n)
            if not os.path.isfile(path):
                continue
            candidates.append(path)

        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in candidates:
            if _is_stable_file(path):
                log.info("verify_download: success %s (%s bytes)", path, os.path.getsize(path))
                return True, os.path.basename(path)

        time.sleep(poll_interval)

    try:
        listing = os.listdir(download_dir)
    except OSError:
        listing = []
    log.warning(
        "verify_download: TIMEOUT after %ss — dir has %s: %s",
        int(timeout),
        len(listing),
        listing[:20],
    )
    return False, None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    # maxCard = MaxCredit(config.max_username, config.max_password)
    # maxCard.download()
    b = Bank(config.bank_username, config.bank_password)
    # b.download("holdings")
    # b.download("osh")
    b.download('credit')
    # d = IsracardCredit(config.credit_username, config.credit_password, config.credit_last6)
    # d.download()

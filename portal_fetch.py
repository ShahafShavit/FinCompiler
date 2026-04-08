import urllib.parse
from datetime import datetime, timedelta
import config
from selenium import webdriver, common
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Keys, ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import time

from folder_tracking import FolderTracker

project_dir = os.path.abspath(os.path.dirname(__file__))
download_dir = os.path.join(project_dir, config.input_dir)

# Set FINANCE_SELENIUM_DEBUG=1 to print optional-click outcomes and pause hints.
# Set FINANCE_SELENIUM_PAUSE=1 to wait for Enter after each flow_debug_pause(...) call.


def _selenium_debug() -> bool:
    return os.environ.get("FINANCE_SELENIUM_DEBUG", "").strip() in ("1", "true", "yes")


def _flow_debug_log(message: str) -> None:
    if _selenium_debug():
        print(f"[selenium] {message}")


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
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        el.click()
        if description:
            _flow_debug_log(f"optional click OK: {description}")
        return True
    except TimeoutException:
        if description:
            _flow_debug_log(f"optional click skipped (not found in {timeout}s): {description}")
        return False


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
        print(f"TransactionFile renamed to: {new_filename}")
    else:
        print("No files with the specified extension found.")


def load_driver():
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
    return driver


class Bank:
    def __init__(self, username, password):
        self.__username = username
        self.__password = password
        self.__driver = load_driver()
        self.__driver = self.__login__()

    def __login__(self):
        url = 'https://hb2.bankleumi.co.il/H/Login.html'
        self.__driver.get(url)
        optional_click(
            self.__driver,
            (By.CSS_SELECTOR, "button.app-close-cookies-btn"),
            timeout=5,
            description="Leumi login — cookie banner close",
        )
        flow_debug_pause(self.__driver, "Leumi login page loaded (after cookie dismiss attempt)")
        time.sleep(2)
        username = self.__driver.find_element(By.NAME, 'user')
        password = self.__driver.find_element(By.NAME, 'password')
        time.sleep(1)
        username.send_keys(self.__username)
        time.sleep(1)
        password.send_keys(self.__password)
        submit_button = WebDriverWait(self.__driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(@class, 'cursor-pointer')]"))
        )
        submit_button.click()

        WebDriverWait(self.__driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//span[contains(@class, 'welcome-text')]"))
        )
        return self.__driver

    def download(self, file: str, from_date=None, to_date=None):
        if file.lower() == 'holdings':
            self.__driver.get('https://hb2.bankleumi.co.il/ebanking/Accounts/DisplayBalances.aspx#/')

            export_button = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.ID, 'BTNEXCEL'))
            )
            export_button.click()

            WebDriverWait(self.__driver, 10).until(EC.number_of_windows_to_be(2))
            self.__driver.switch_to.window(self.__driver.window_handles[1])

            download_btn = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.ID, 'ImgContinue'))
            )
            folder_tracker = FolderTracker(download_dir)
            result = folder_tracker.monitor_folder(1, 10)
            download_btn.click()
            if result:
                print("File downloaded successfully.")
            else:
                print("File download failed or timed out.")

            time.sleep(2)
        elif file.lower() == 'osh':
            self.__driver.switch_to.window(self.__driver.window_handles[0])

            self.__driver.get('https://hb2.bankleumi.co.il/ebanking/SO/SPA.aspx#/ts/BusinessAccountTrx?WidgetPar=1')
            advanced_search_button = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@title='חיפוש מתקדם']"))
            )
            advanced_search_button.click()

            period_span = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='תקופה']"))
            )
            period_span.click()

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
                # date_str = "01.01.25"
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

            filter_button = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='סנן']"))
            )
            filter_button.click()

            time.sleep(5)
            export_button = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@title='יצוא לאקסל']"))
            )
            export_button.click()

            modal_dialog = WebDriverWait(self.__driver, 10).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "modal-dialog"))
            )

            buttons = modal_dialog.find_elements(By.TAG_NAME, "button")

            continue_btn = None
            for i, button in enumerate(buttons):
                if 'המשך' in button.text:
                    continue_btn = button
                    break

            if continue_btn:
                actions = ActionChains(self.__driver)
                actions.move_to_element(continue_btn).click().perform()

            else:
                print("Continue button not found")
        elif file.lower() == 'credit':
            self.__driver.get('https://hb2.bankleumi.co.il/ebanking/SO/SPA.aspx#/ts/CardsWorld')

            WebDriverWait(self.__driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.swiper-wrapper"))
            )

            cards = self.__driver.find_elements(By.CSS_SELECTOR,
                                                "div.swiper-slide:not(.blank) article.credit-card-item")

            for index, card in enumerate(cards):
                amount_text = card.find_element(By.CSS_SELECTOR, "span.ng-star-inserted").text
                if amount_text == '':
                    continue
                if float(amount_text.replace(',', '')) <= 0:
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
                d = DirTracker(download_dir)
                current_url = self.__driver.current_url
                domain = urllib.parse.urlparse(current_url).netloc
                if "isracard.co.il" in domain:
                    while True:
                        select_button = WebDriverWait(self.__driver, 10).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "span[aria-label='בחר מועד חיוב מועד קרוב']"))
                        )
                        select_button.click()

                        select_option = WebDriverWait(self.__driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH, "//div[@id='ui-select-choices-row-3-1']"))
                        )
                        select_option.click()

                        download_button = WebDriverWait(self.__driver, 10).until(
                            EC.element_to_be_clickable(
                                (By.XPATH, "//button[@aria-label='Excel הורד פירוט חיובים בפורמט']"))
                        )
                        download_button.click()
                        time.sleep(4)
                        if d.new_file()[0]:
                            self.__driver.close()  # Close the current window
                            self.__driver.switch_to.window(self.__driver.window_handles[0])
                            break
                if "max.co.il" in domain:
                    while True:
                        months_hebrew = [
                            "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
                            "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"
                        ]
                        current_date = datetime.now()

                        next_month_index = current_date.month
                        current_year = current_date.year

                        this_month_string = f"{months_hebrew[next_month_index]} {current_year}"
                        print(f"{next_month_index=} {current_year=} {this_month_string=}")
                        date_combo = WebDriverWait(self.__driver, 10).until(
                            EC.element_to_be_clickable(
                                (By.XPATH,
                                 f"//div[contains(@class, 'combo-text dates') and text()=' {this_month_string} ']"))
                        )
                        date_combo.click()

                        previous_month = WebDriverWait(self.__driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH,
                                                        "//li[@class='month selected-month ng-star-inserted']/following-sibling::li[1]"))
                        )
                        previous_month.click()
                        excel_link = WebDriverWait(self.__driver, 10).until(
                            EC.element_to_be_clickable(
                                (By.XPATH, "//a[contains(text(),'להורדת פירוט החיובים כקובץ אקסל')]"))
                        )
                        excel_link.click()
                        time.sleep(4)
                        if d.new_file()[0]:
                            self.__driver.close()
                            self.__driver.switch_to.window(self.__driver.window_handles[0])
                            break
        else:
            raise Exception("TransactionFile must be 'holdings' or 'osh'.")
        downloaded, new_file = verify_download(download_dir, 15, 60)

        if downloaded:
            print(
                f"{file.capitalize()} was downloaded successfully. at {os.path.join(download_dir, os.path.basename(new_file))}")
        else:
            raise FileNotFoundError(f"Failed to download {file.capitalize()} file.")
        return True

    def __del__(self):
        self.__driver.quit()


class IsracardCredit:
    def __init__(self, username, password, last6):
        self.__username = username
        self.__last6 = last6
        self.__password = password
        self.__driver = load_driver()
        self.__driver = self.__login()

    def __login(self):
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
        return self.__driver

    def download(self):
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
        link.click()
        time.sleep(5)

        downloaded, new_file = verify_download(download_dir, 15, 60)
        if downloaded:
            print(f"{new_file} was downloaded successfully. at {os.path.join(download_dir, new_file)}")
        else:
            raise FileNotFoundError(f"Failed to download {new_file}.")
        return True

    def __del__(self):
        self.__driver.quit()


class MaxCredit:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.__driver = load_driver()
        self.__driver = self.__login()

    def __login(self):
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
        return self.__driver

    def download(self, card_digits=None):
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
        if not card_digits:
            for card in card_digits_list:
                time.sleep(1)
                element = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), {card})]"))
                )
                element.click()
                time.sleep(1)
                element = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'ייצא לאקסל')]"))
                )
                time.sleep(2)
                element.click()
                time.sleep(10)
                downloaded, new_file = verify_download(download_dir, 15, 60)
                if downloaded:
                    print(f"{new_file} was downloaded successfully. at {os.path.join(download_dir, new_file)}")
                else:
                    raise FileNotFoundError(f"Failed to download {new_file}.")

                timestamp_latest_file_in_dir(download_dir, ".xlsx")

                element = WebDriverWait(self.__driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{card}')]"))
                )
                element.click()
        elif str(card_digits) in card_digits_list:
            card = str(card_digits)
            time.sleep(1)
            element = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), {card})]"))
            )
            element.click()
            time.sleep(1)
            element = WebDriverWait(self.__driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'ייצא לאקסל')]"))
            )
            element.click()
            time.sleep(10)
            downloaded, new_file = verify_download(download_dir, 15, 30)
            if downloaded:
                print(f"{new_file} was downloaded successfully. at {os.path.join(download_dir, new_file)}")
            else:
                raise FileNotFoundError(f"Failed to download {new_file}.")

            timestamp_latest_file_in_dir(download_dir, ".xlsx")
            time.sleep(5)
        else:
            raise Exception(f"Card digits {card_digits} not found within list inside website")

    def __del__(self):
        self.__driver.quit()


def verify_download(download_dir, check_interval, timeout):
    """
    Verify if a new file has been downloaded within the last `timeout` seconds.
    Args:
        download_dir (str): The directory where files are downloaded.
        check_interval (int): Time in seconds to wait between download checks.
        timeout (int): Time in seconds to wait for the download to complete.

    Returns:
        bool: True if a new file has been downloaded, False otherwise.
    """

    def get_latest_file(directory):
        """
        Get the latest file from the specified directory.

        Args:
            directory (str): The directory to check for files.

        Returns:
            str: The path to the latest file or None if the directory is empty.
        """
        files = os.listdir(directory)
        paths = [os.path.join(directory, basename) for basename in files]
        return max(paths, key=os.path.getctime) if paths else None

    end_time = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < end_time:
        latest_file = get_latest_file(download_dir)
        if latest_file:
            file_creation_time = datetime.fromtimestamp(os.path.getctime(latest_file))
            current_time = datetime.now()
            time_difference = current_time - file_creation_time
            if time_difference < timedelta(seconds=check_interval):
                return True, os.path.basename(latest_file)
        time.sleep(check_interval / 2)
    return False, None


class DirTracker:
    def __init__(self, dir: str):
        self.directory = dir
        self.__filelist__ = set(os.listdir(dir))

    def new_file(self):
        current: set = set(os.listdir(self.directory))
        return len(current.symmetric_difference(self.__filelist__)) != 0, current.symmetric_difference(
            self.__filelist__)


if __name__ == "__main__":

    # maxCard = MaxCredit(config.max_username, config.max_password)
    # maxCard.download()
    b = Bank(config.bank_username, config.bank_password)
    # b.download("holdings")
    # b.download("osh")
    b.download('credit')
    # d = IsracardCredit(config.credit_username, config.credit_password, config.credit_last6)
    # d.download()

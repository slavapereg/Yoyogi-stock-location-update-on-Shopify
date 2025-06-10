import os
import time
import json
import logging
import requests
import pandas as pd
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import glob
import shutil
from tenacity import retry, stop_after_attempt, wait_exponential

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration (use environment variables for GitHub Actions)
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
TARGET_LOCATION_ID = os.getenv('TARGET_LOCATION_ID', "23455432785")
CSV_FILENAME = 'latest_stock.csv'
DOWNLOAD_DIR = '/tmp'
BATCH_SIZE = 35

# FLAM credentials from environment
FLAM_USERNAME = os.getenv('FLAM_USERNAME')
FLAM_PASSWORD = os.getenv('FLAM_PASSWORD')
FLAM_URL = os.getenv('FLAM_URL', 'https://chefworksjp.flam.bz/login')

# Setup session with retries
session = requests.Session()
retries = requests.adapters.Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST", "GET"]
)
session.mount('https://', requests.adapters.HTTPAdapter(max_retries=retries))
session.headers.update({
    'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
})

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    # Use the pre-installed chromedriver on GitHub Actions
    driver_path = shutil.which("chromedriver")
    driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
    return driver

def rename_latest_csv(download_dir):
    list_of_files = glob.glob(os.path.join(download_dir, "*.csv"))
    if not list_of_files:
        logger.warning("No CSV file found to rename.")
        return
    latest_file = max(list_of_files, key=os.path.getctime)
    date_str = datetime.now().strftime("%Y%m%d")
    new_name = f"{date_str}_stock_export.csv"
    new_path = os.path.join(download_dir, new_name)
    shutil.move(latest_file, new_path)
    logger.info(f"Renamed {latest_file} to {new_path}")
    return new_path

def download_csv():
    logger.info("Starting CSV download process...")
    driver = setup_driver()
    wait = WebDriverWait(driver, 20)
    try:
        logger.info("Navigating to FLAM login page...")
        driver.get(FLAM_URL)
        time.sleep(2)

        logger.info("Entering username...")
        username_field = wait.until(EC.presence_of_element_located((By.ID, 'loginid')))
        username_field.clear()
        username_field.send_keys(FLAM_USERNAME)

        logger.info("Entering password...")
        password_field = driver.find_element(By.ID, 'password')
        password_field.clear()
        password_field.send_keys(FLAM_PASSWORD)

        logger.info("Clicking login button...")
        login_button = driver.find_element(By.ID, 'btn_login')
        login_button.click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        logger.info("Successfully logged in!")

        # Go to export page
        stock_export_url = 'https://chefworksjp.flam.bz/stockrecents/export?sh=&p=&pc=&pcd=&gs=1&nz=&sn=&lt=&gsn=&gln=&sast=&exc_sd=&exc_ed=&exc_sq=&exc_sq_eq=&s_ship=&s_sale=&s_arrival=&s_purchase=&s_receiptpayschedule=&s_receiptpay=&l_return='
        driver.get(stock_export_url)
        time.sleep(2)

        # Fill both warehouse fields with 'W1'
        logger.info("Filling in both warehouse fields with 'W1'...")
        warehouse_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
        if len(warehouse_inputs) < 2:
            logger.error(f"Expected at least 2 warehouse input fields but found {len(warehouse_inputs)}.")
            return None
        for i in range(2):
            warehouse_inputs[i].clear()
            warehouse_inputs[i].send_keys("W1")

        # Untick the 集計 checkbox if checked
        logger.info("Ensuring 集計 checkbox is unticked...")
        checkboxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
        if checkboxes and checkboxes[0].is_selected():
            checkboxes[0].click()

        # Click the ダウンロード (Download) button
        logger.info("Clicking download button...")
        download_btn = wait.until(EC.element_to_be_clickable((By.ID, 'btn_download')))
        download_btn.click()
        time.sleep(1)

        # Click the CSV形式(.csv) option by visible text
        logger.info("Clicking CSV format option by visible text...")
        csv_btn = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, 'CSV形式(.csv)')))
        csv_btn.click()
        logger.info("CSV format link clicked. Waiting for download...")
        time.sleep(15)

        # Rename the downloaded file and get its path
        csv_path = rename_latest_csv(DOWNLOAD_DIR)
        if csv_path:
            # Copy to current directory
            shutil.copy2(csv_path, CSV_FILENAME)
            logger.info(f"CSV downloaded and copied to {CSV_FILENAME}")
            return True
        return False

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = f"/tmp/flam_error_{timestamp}.png"
        driver.save_screenshot(screenshot_path)
        logger.error(f"Screenshot saved to {screenshot_path}")
        return False
    finally:
        driver.quit()

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60))
def get_variants_bulk(skus):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-04/graphql.json"
    sku_conditions = " OR ".join([f'sku:{sku}' for sku in skus])
    query = f"""
    {{
      productVariants(first: 250, query: "{sku_conditions}") {{
        edges {{
          node {{
            id
            sku
            product {{
              id
              title
              handle
            }}
            inventoryItem {{
              id
              inventoryLevels(first: 1, query: \"location_id:23455432785\") {{
                edges {{
                  node {{
                    quantities(names: [\"available\", \"committed\"]) {{
                      name
                      quantity
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    response = session.post(url, json={'query': query})
    if response.status_code == 200:
        data = response.json()
        variants = data.get('data', {}).get('productVariants', {}).get('edges', [])
        if variants:
            return [edge['node'] for edge in variants]
    return None

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60))
def update_inventory_bulk(updates):
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-04/graphql.json"
    mutations = []
    for update in updates:
        inventory_item_id = update['inventory_item_id'].split('/')[-1]
        location_id = TARGET_LOCATION_ID.split('/')[-1]
        available = int(round(update['available']))
        mutations.append(f"""
          inventorySetOnHandQuantities{len(mutations)}: inventorySetOnHandQuantities(input: {{
            setQuantities: [
              {{
                inventoryItemId: \"gid://shopify/InventoryItem/{inventory_item_id}\",
                locationId: \"gid://shopify/Location/{location_id}\",
                quantity: {available}
              }}
            ],
            reason: \"correction\"
          }}) {{
            inventoryAdjustmentGroup {{
              id
              changes {{
                name
                delta
                location {{
                  name
                }}
              }}
            }}
            userErrors {{
              field
              message
            }}
          }}
        """)
    mutation = f"""
    mutation {{
      {''.join(mutations)}
    }}
    """
    response = session.post(url, json={'query': mutation})
    if response.status_code == 200:
        data = response.json()
        results = []
        for i, update in enumerate(updates):
            result = data.get('data', {}).get(f'inventorySetOnHandQuantities{i}', {})
            user_errors = result.get('userErrors', [])
            if user_errors:
                results.append({
                    'sku': update['sku'],
                    'status': 'error',
                    'message': str(user_errors),
                    'product_title': update['product_title'],
                    'variant_id': update['variant_id']
                })
            else:
                results.append({
                    'sku': update['sku'],
                    'status': 'updated',
                    'message': 'Successfully updated',
                    'product_title': update['product_title'],
                    'variant_id': update['variant_id']
                })
        return results
    return None

def extract_numeric_id(gid):
    if gid and 'gid://' in gid:
        return gid.split('/')[-1]
    return gid

def process_sku_batch(skus, stock_data_dict):
    try:
        variants = get_variants_bulk(skus)
        if not variants:
            return [{
                'sku': sku,
                'status': 'error',
                'message': 'SKU not found in Shopify',
                'csv_stock': stock_data_dict[sku]['current_stock'],
                'csv_available': stock_data_dict[sku]['available_for_sale'],
                'shopify_available': None,
                'shopify_committed': None,
                'new_available': None,
                'product_title': None,
                'variant_id': None,
                'product_handle': None,
                'product_id': None
            } for sku in skus]
        updates = []
        results = []
        found_skus = set()
        sku_variants = {}
        for variant in variants:
            sku = variant['sku']
            found_skus.add(sku)
            if sku not in sku_variants:
                sku_variants[sku] = []
            sku_variants[sku].append(variant)
            if variant['product']['archived']:
                results.append({
                    'sku': sku,
                    'status': 'skipped',
                    'message': 'Product is archived in Shopify',
                    'csv_stock': stock_data_dict[sku]['current_stock'],
                    'csv_available': stock_data_dict[sku]['available_for_sale'],
                    'shopify_available': None,
                    'shopify_committed': None,
                    'new_available': None,
                    'product_title': variant['product']['title'],
                    'variant_id': extract_numeric_id(variant['id']),
                    'product_handle': variant['product']['handle'],
                    'product_id': extract_numeric_id(variant['product']['id'])
                })
                continue
            if sku not in stock_data_dict:
                continue
            stock_data = stock_data_dict[sku]
            quantities = {q['name']: q['quantity'] for q in variant['inventoryItem']['inventoryLevels']['edges'][0]['node']['quantities']}
            current_available = quantities.get('available', 0)
            current_committed = quantities.get('committed', 0)
            if stock_data['expected_arrival'] == stock_data['expected_shipment']:
                new_available = stock_data['current_stock']
            else:
                new_available = stock_data['available_for_sale']
            if new_available < 0:
                new_available = 0
            if stock_data['available_for_sale'] <= 0 and current_available == 0:
                results.append({
                    'sku': sku,
                    'status': 'skipped',
                    'message': 'Both CSV and Shopify show 0 stock',
                    'csv_stock': stock_data['current_stock'],
                    'csv_available': stock_data['available_for_sale'],
                    'shopify_available': current_available,
                    'shopify_committed': current_committed,
                    'new_available': new_available,
                    'product_title': variant['product']['title'],
                    'variant_id': extract_numeric_id(variant['id']),
                    'product_handle': variant['product']['handle'],
                    'product_id': extract_numeric_id(variant['product']['id'])
                })
                continue
            if current_available == new_available:
                results.append({
                    'sku': sku,
                    'status': 'skipped',
                    'message': 'Shopify already matches calculated value',
                    'csv_stock': stock_data['current_stock'],
                    'csv_available': stock_data['available_for_sale'],
                    'shopify_available': current_available,
                    'shopify_committed': current_committed,
                    'new_available': new_available,
                    'product_title': variant['product']['title'],
                    'variant_id': extract_numeric_id(variant['id']),
                    'product_handle': variant['product']['handle'],
                    'product_id': extract_numeric_id(variant['product']['id'])
                })
                continue
            updates.append({
                'sku': sku,
                'inventory_item_id': variant['inventoryItem']['id'],
                'available': new_available,
                'product_title': variant['product']['title'],
                'variant_id': extract_numeric_id(variant['id']),
                'current_available': current_available,
                'current_committed': current_committed,
                'csv_stock': stock_data['current_stock'],
                'csv_available': stock_data['available_for_sale'],
                'product_handle': variant['product']['handle'],
                'product_id': extract_numeric_id(variant['product']['id'])
            })
        for sku in skus:
            if sku not in found_skus:
                results.append({
                    'sku': sku,
                    'status': 'error',
                    'message': 'SKU not found in Shopify',
                    'csv_stock': stock_data_dict[sku]['current_stock'],
                    'csv_available': stock_data_dict[sku]['available_for_sale'],
                    'shopify_available': None,
                    'shopify_committed': None,
                    'new_available': None,
                    'product_title': None,
                    'variant_id': None,
                    'product_handle': None,
                    'product_id': None
                })
        if updates:
            update_results = update_inventory_bulk(updates)
            if update_results:
                for result in update_results:
                    update = next((u for u in updates if u['sku'] == result['sku'] and u['variant_id'] == result['variant_id']), None)
                    if update:
                        result.update({
                            'csv_stock': update['csv_stock'],
                            'csv_available': update['csv_available'],
                            'shopify_available': update['current_available'],
                            'shopify_committed': update['current_committed'],
                            'new_available': update['available'],
                            'product_handle': update['product_handle'],
                            'product_id': update['product_id']
                        })
                results.extend(update_results)
        for sku, variants in sku_variants.items():
            if len(variants) > 1:
                for result in results:
                    if result['sku'] == sku:
                        result['multiple_variants'] = True
                        result['variant_count'] = len(variants)
                        result['variant_products'] = [v['product']['title'] for v in variants]
                        break
        return results
    except Exception as e:
        logger.error(f"Error processing SKU batch: {e}")
        return [{
            'sku': sku,
            'status': 'error',
            'message': str(e),
            'csv_stock': stock_data_dict[sku]['current_stock'],
            'csv_available': stock_data_dict[sku]['available_for_sale'],
            'shopify_available': None,
            'shopify_committed': None,
            'new_available': None,
            'product_title': None,
            'variant_id': None,
            'product_handle': None,
            'product_id': None
        } for sku in skus]

def main():
    if not download_csv():
        logger.error("Failed to download CSV. Exiting.")
        return
    logger.info("Reading CSV data...")
    try:
        df = pd.read_csv(CSV_FILENAME, dtype=str, encoding='cp932')
        logger.info(f"CSV loaded successfully: {len(df)} rows")
    except Exception as e:
        logger.error(f"Error reading CSV: {e}")
        return
    results = []
    total = len(df)
    try:
        for batch_start in range(0, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            logger.info(f"Processing batch {batch_start}-{batch_end} of {total}")
            batch_skus = []
            stock_data_dict = {}
            for index in range(batch_start, batch_end):
                row = df.iloc[index]
                sku = row['商品コード']
                if pd.isna(sku) or sku == '':
                    continue
                try:
                    current_stock = float(str(row['現在在庫数']).strip().replace(',', '').replace('　', '').replace(' ', ''))
                    expected_arrival = float(str(row['入庫予定数']).strip().replace(',', '').replace('　', '').replace(' ', ''))
                    expected_shipment = float(str(row['出庫予定数']).strip().replace(',', '').replace('　', '').replace(' ', ''))
                    available_for_sale = float(str(row['販売可能数']).strip().replace(',', '').replace('　', '').replace(' ', ''))
                    batch_skus.append(sku)
                    stock_data_dict[sku] = {
                        'current_stock': current_stock,
                        'expected_arrival': expected_arrival,
                        'expected_shipment': expected_shipment,
                        'available_for_sale': available_for_sale
                    }
                except Exception as e:
                    logger.error(f"Error processing SKU {sku}: {e}")
                    continue
            if batch_skus:
                batch_results = process_sku_batch(batch_skus, stock_data_dict)
                results.extend(batch_results)
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
        return
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return
    report_df = pd.DataFrame(results)
    logger.info("\nUpdate Summary:")
    logger.info(f"Total variants processed: {len(results)}")
    logger.info(f"Updated: {len([r for r in results if r['status'] == 'updated'])}")
    logger.info(f"Skipped: {len([r for r in results if r['status'] == 'skipped'])}")
    logger.info(f"Errors: {len([r for r in results if r['status'] == 'error'])}")
    logger.info("\nDetailed Report:")
    logger.info("=" * 100)
    updated = report_df[report_df['status'] == 'updated']
    if not updated.empty:
        logger.info("\nUpdated SKUs:")
        logger.info("-" * 100)
        for _, row in updated.iterrows():
            logger.info(f"SKU: {row['sku']}")
            logger.info(f"Product: {row['product_title']}")
            logger.info(f"CSV Stock: {row['csv_stock']}")
            logger.info(f"CSV Available: {row['csv_available']}")
            logger.info(f"Shopify Available: {row['shopify_available']}")
            logger.info(f"New Available: {row['new_available']}")
            if row.get('multiple_variants'):
                logger.info(f"WARNING: This SKU appears in {row['variant_count']} products:")
                for product in row['variant_products']:
                    logger.info(f"  - {product}")
            logger.info("-" * 100)
    skipped = report_df[report_df['status'] == 'skipped']
    if not skipped.empty:
        logger.info("\nSkipped SKUs:")
        logger.info("-" * 100)
        for _, row in skipped.iterrows():
            logger.info(f"SKU: {row['sku']}")
            logger.info(f"Product: {row['product_title']}")
            logger.info(f"Reason: {row['message']}")
            logger.info(f"CSV Stock: {row['csv_stock']}")
            logger.info(f"CSV Available: {row['csv_available']}")
            logger.info(f"Shopify Available: {row['shopify_available']}")
            if row.get('multiple_variants'):
                logger.info(f"WARNING: This SKU appears in {row['variant_count']} products:")
                for product in row['variant_products']:
                    logger.info(f"  - {product}")
            logger.info("-" * 100)
    errors = report_df[report_df['status'] == 'error']
    if not errors.empty:
        logger.info("\nError SKUs:")
        logger.info("-" * 100)
        for _, row in errors.iterrows():
            logger.info(f"SKU: {row['sku']}")
            logger.info(f"Product: {row['product_title']}")
            logger.info(f"Error: {row['message']}")
            logger.info(f"CSV Stock: {row['csv_stock']}")
            logger.info(f"CSV Available: {row['csv_available']}")
            if row.get('multiple_variants'):
                logger.info(f"WARNING: This SKU appears in {row['variant_count']} products:")
                for product in row['variant_products']:
                    logger.info(f"  - {product}")
            logger.info("-" * 100)
    # Print summary of multiple variants
    if 'multiple_variants' in report_df.columns:
        multiple_variants = report_df[report_df['multiple_variants'] == True]
    else:
        multiple_variants = pd.DataFrame()
    if not multiple_variants.empty:
        logger.info("\nSKUs with Multiple Variants:")
        logger.info("-" * 100)
        for _, row in multiple_variants.iterrows():
            logger.info(f"SKU: {row['sku']}")
            logger.info(f"Status: {row['status']}")
            logger.info(f"Appears in {row['variant_count']} products:")
            for product in row['variant_products']:
                logger.info(f"  - {product}")
            logger.info("-" * 100)

if __name__ == "__main__":
    main() 
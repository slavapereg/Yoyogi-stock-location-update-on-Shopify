from update_all_stock_github import get_variants_bulk

if __name__ == "__main__":
    test_skus = ['122A-BLK-0']
    variants = get_variants_bulk(test_skus)
    print("Variants returned from Shopify:")
    if variants:
        for v in variants:
            print(f"SKU: {v['sku']}")
            print(f"Product Title: {v['product']['title']}")
            print(f"Product Handle: {v['product']['handle']}")
            print(f"Product ID: {v['product']['id']}")
            print(f"Variant ID: {v['id']}")
            print()
    else:
        print("No variants found.") 
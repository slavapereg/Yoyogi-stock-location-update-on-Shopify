from update_all_stock_github import session, SHOPIFY_STORE_URL

def test_raw_shopify_query():
    sku = "122A-BLK-0"
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-04/graphql.json"
    sku_conditions = f"sku:{sku}"
    query = f'''
    {{
      productVariants(first: 10, query: "{sku_conditions}") {{
        edges {{
          node {{
            id
            sku
            product {{
              id
              title
              archived
            }}
          }}
        }}
      }}
    }}
    '''
    response = session.post(url, json={'query': query})
    print("Status code:", response.status_code)
    print("Raw response:", response.text)

if __name__ == "__main__":
    test_raw_shopify_query() 
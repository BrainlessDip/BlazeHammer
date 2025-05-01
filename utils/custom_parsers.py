 # Custom parsers for different status codes
def parse_200(response):
  Json = response.json()
  msg = Json.get('msg')
  return f"- {msg}"

# Custom response parser for 302 Found
def parse_302(response):
  location = response.headers.get('Location', 'No Location header found')
  return f"- Redirected to: {location}"

custom_response_parsers = {
  'all': parse_302,
  200: parse_200
}

# Custom payload parser for 302 Found
def payload_parse(json):
   return json

custom_payload_parsers = {
  'all': payload_parse,
  200: payload_parse
}
# Custom headers parser for 302 Found
def headers_parse(json):
   return json['Referer']

custom_headers_parsers = {
  302: headers_parse
}
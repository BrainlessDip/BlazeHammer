 # Custom parsers for different status codes
def parse_200(response):
  return f"- {response}"

custom_response_parsers = {
  'all': parse_200,
  200: parse_200
}

# Custom payload parser
def payload_parse(json):
   return json

custom_payload_parsers = {
  'all': payload_parse,
  200: payload_parse
}

# Custom headers parser
def headers_parse(json):
   return json

custom_headers_parsers = {
  "all": headers_parse,
  200: headers_parse
}
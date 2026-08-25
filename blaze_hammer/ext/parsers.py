# Custom parsers for different status codes.
#
# Edit this file to change how -pp/--print-payload, -pr/--print-response and
# -ph/--print-headers render output. Each dict maps an HTTP status code to a
# callable; the special "all" key is the fallback for every other status.
# A parser that raises is reported but never crashes the run.


def parse_200(response):
    return f"- {response}"


custom_response_parsers = {"all": parse_200, 200: parse_200}


def payload_parse(json):
    return json


custom_payload_parsers = {"all": payload_parse, 200: payload_parse}


def headers_parse(json):
    return json


custom_headers_parsers = {"all": headers_parse, 200: headers_parse}

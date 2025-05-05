## process cloudflate data

import json
import boto3
import random
import time
import os
from datetime import datetime, timezone
from typing import Dict, List, Any
import ipaddress
from random import randint, uniform

# Initialize boto3 client
kinesis = boto3.client('kinesis')

# Sample location data for randomization
LOCATIONS = [
    {
        "city": "San Francisco",
        "colo": "SFO",
        "country": "US",
        "latitude": "37.7749",
        "longitude": "-122.4194",
        "postalCode": "94105",
        "region": "California",
        "timezone": "America/Los_Angeles"
    },
    {
        "city": "New York",
        "colo": "NYC",
        "country": "US",
        "latitude": "40.7128",
        "longitude": "-74.0060",
        "postalCode": "10001",
        "region": "New York",
        "timezone": "America/New_York"
    },
    {
        "city": "Chicago",
        "colo": "ORD",
        "country": "US",
        "latitude": "41.8781",
        "longitude": "-87.6298",
        "postalCode": "60601",
        "region": "Illinois",
        "timezone": "America/Chicago"
    },
    {
        "city": "Seattle",
        "colo": "SEA",
        "country": "US",
        "latitude": "47.6062",
        "longitude": "-122.3321",
        "postalCode": "98101",
        "region": "Washington",
        "timezone": "America/Los_Angeles"
    }
]

# Sample network providers
NETWORK_PROVIDERS = [
    ("Comcast Cable", 7922),
    ("AT&T", 7018),
    ("Verizon", 701),
    ("Charter Communications", 20115),
    ("Cox Communications", 22773)
]

def generate_random_ip() -> str:
    """Generate a random IP address"""
    return str(ipaddress.IPv4Address(random.randint(0, 2**32 - 1)))

def generate_random_uuid() -> str:
    """Generate a random UUID"""
    import uuid
    return str(uuid.uuid4())

def modify_payload(base_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Modify the base payload with random data"""
    modified = base_payload.copy()
    
    # Generate new timestamp with slight variation
    base_time = datetime.now(timezone.utc)
    time_variation = random.randint(-3600, 3600)  # ±1 hour
    new_timestamp = (base_time.timestamp() + time_variation)
    timestamp_str = datetime.fromtimestamp(new_timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')
    
    # Update metadata
    modified['metadata']['cloudflare_worker']['id'] = generate_random_uuid()
    modified['metadata']['cloudflare_worker']['started'] = timestamp_str
    modified['metadata']['ip_address'] = generate_random_ip()
    
    # Update location
    new_location = random.choice(LOCATIONS)
    modified['metadata']['location'] = new_location
    
    # Update network information
    provider, asn = random.choice(NETWORK_PROVIDERS)
    modified['metadata']['network']['asOrganization'] = provider
    modified['metadata']['network']['asn'] = asn
    modified['metadata']['network']['clientTrustScore'] = random.randint(85, 100)
    
    # Update timestamp
    modified['timestamp'] = timestamp_str
    
    # Update response headers
    modified['response']['headers']['cf-ray'] = f"{generate_random_uuid()[:8]}-{new_location['colo']}"
    modified['response']['headers']['date'] = datetime.fromtimestamp(new_timestamp, timezone.utc).strftime(
        '%a, %d %b %Y %H:%M:%S GMT'
    )
    
    # Update request headers
    modified['request']['headers']['cf-ray'] = f"{generate_random_uuid()[:8]}-{new_location['colo']}"
    modified['request']['headers']['cf-connecting-ip'] = modified['metadata']['ip_address']
    modified['request']['headers']['true-client-ip'] = modified['metadata']['ip_address']
    modified['request']['headers']['x-real-ip'] = modified['metadata']['ip_address']
    
    return modified

def create_multiple_records(base_payload: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
    """Create multiple records with variations"""
    return [modify_payload(base_payload) for _ in range(count)]

def put_records_to_kinesis(stream_name: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Put multiple records into Kinesis stream"""
    print(f"Attempting to put {len(records)} records to stream: {stream_name}")  # Add logging
    
    kinesis_records = [
        {
            'Data': json.dumps(record).encode('utf-8'),
            'PartitionKey': str(random.randint(1, 100))
        }
        for record in records
    ]
    
    try:
        response = kinesis.put_records(
            Records=kinesis_records,
            StreamName=stream_name
        )
        
        # Add detailed logging
        print(f"Kinesis response: {json.dumps(response, default=str)}")
        
        failed_count = response.get('FailedRecordCount', 0)
        if failed_count > 0:
            print(f"Warning: {failed_count} records failed to be written")
            # Log failed records details
            for idx, record in enumerate(response.get('Records', [])):
                if 'ErrorCode' in record:
                    print(f"Record {idx} failed: {record['ErrorCode']} - {record['ErrorMessage']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Successfully put {len(records)} records to Kinesis',
                'failed_record_count': failed_count,
                'stream_name': stream_name,
                'response_details': response
            })
        }
    except Exception as e:
        print(f"Error putting records to Kinesis: {str(e)}")  # Add error logging
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Error putting records to Kinesis',
                'stream_name': stream_name
            })
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler"""
    try:
        # Get stream name from environment variable
        stream_name = os.environ['KINESIS_STREAM_NAME']
        
        # Get base payload from the event
        base_payload = event.get('payload', {})
        
        # Get number of records to generate (default to 10 if not specified)
        record_count = event.get('record_count', 10)
        
        # Validate record count
        if not 1 <= record_count <= 500:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'record_count must be between 1 and 500'
                })
            }
        
        # Generate multiple records with variations
        records = create_multiple_records(base_payload, record_count)
        
        # Put records to Kinesis
        return put_records_to_kinesis(stream_name, records)
        
    except KeyError as e:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': f'Missing required parameter: {str(e)}'
            })
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Internal server error'
            })
        }

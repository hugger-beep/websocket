``` mermaid

sequenceDiagram
    participant Client as WebSocket Client
    participant API as API Gateway WebSocket
    participant Lambda as Lambda Function
    participant SM as Secrets Manager
    participant Cognito as Cognito User Pool
    participant Kinesis as Kinesis Stream

    Client->>+API: WebSocket Connect
    API->>+Lambda: Route $connect event
    
    Lambda->>+SM: Get Secret Value
    SM-->>-Lambda: Return Credentials
    
    Lambda->>+Cognito: Initiate Authentication
    Cognito-->>-Lambda: Return Access Token
    Lambda-->>-API: Connection Successful (200)
    API-->>Client: Connection Established

    Client->>+API: Send "getRecords" Action
    API->>+Lambda: Route getRecords event
    
    Lambda->>+Kinesis: Get Shard Iterator
    Kinesis-->>-Lambda: Return Shard Iterator
    
    Lambda->>+Kinesis: Get Records
    Kinesis-->>-Lambda: Return Records Data
    
    Lambda->>Lambda: Process Records
    
    Lambda-->>-API: Send Processed Records
    API-->>-Client: Deliver WebSocket Message with Records

    Note over Client,API: Connection remains open for further requests
```

# BibleVerse API

A RESTful API service that generates random Bible verses using Spring Boot and AWS Lambda. The service provides both CSB (Christian Standard Bible) and Greek text for each verse.

## Overview

BibleVerse is a cloud-native application that provides simple API endpoints to fetch random Bible verses. The service is deployed as AWS Lambda functions using Spring Cloud Function and uses S3 for data storage.

## Features

- **Random Bible Verse Generation**: Returns random verses from the entire Bible
- **Multiple Text Versions**: Provides both CSB and Greek text
- **RESTful API**: Clean HTTP endpoints with proper JSON responses
- **Serverless Architecture**: AWS Lambda with API Gateway v2
- **Cloud-Native**: S3 integration for data storage
- **Health Monitoring**: Built-in health check endpoint
- **CORS Support**: Cross-origin resource sharing enabled

## Technology Stack

- **Backend Framework**: Spring Boot 4.1.0
- **Language**: Java 25
- **Cloud Platform**: AWS
  - Lambda (serverless compute with SnapStart)
  - API Gateway v2 (HTTP API)
  - S3 (data storage)
  - Route 53 (DNS)
  - ACM (SSL certificates)
- **Infrastructure**: AWS CDK (TypeScript)
- **Dependencies**:
  - Spring Cloud Function
  - AWS SDK for Java v2
  - Lombok

## API Endpoints

### Base URL

```
https://bibleverse.thonbecker.com
```

### Endpoints

#### 1. Random Bible Verse

**GET** `/`

Returns a random Bible verse with both CSB and Greek text.

**Response:**

```json
{
  "book": "John",
  "chapter": "3",
  "verse": "16",
  "text": {
    "CSB": "For God loved the world in this way: He gave his one and only Son, so that everyone who believes in him will not perish but have eternal life.",
    "Greek": "Οὕτω γὰρ ἠγάπησεν ὁ Θεὸς τὸν κόσμον, ὥστε ἔδωκε τὸν Υἱὸν αὑτοῦ τὸν μονογενῆ, διὰ νὰ μή ἀπολεσθῇ πᾶς ὁ πιστεύων εἰς αὐτόν, ἀλλὰ νὰ ἔχῃ ζωὴν αἰώνιον."
  }
}
```

#### 2. Health Check

**GET** `/about`

Returns service health information and build details.

**Response:**

```json
{
  "status": "healthy",
  "packageName": "bibleverse",
  "version": "2.0.0",
  "time": "2024-01-15T10:30:00Z"
}
```

### Response Headers

All endpoints return:
- `Content-Type: application/json`
- `Access-Control-Allow-Origin: *`
- `Access-Control-Allow-Methods: GET, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type, Access-Control-Allow-Headers, Access-Control-Allow-Origin, Access-Control-Allow-Methods`

## Setup and Deployment

### Prerequisites

- **mise** for local toolchain management
- **Java 25** (Corretto)
- **Maven 3.9** or higher
- **Node.js 24** or higher (for CDK)
- **pnpm 10** (Corepack reads `packageManager` in `.infrastructure/package.json`; `mise install` provides the same pin)
- **AWS CLI** configured with appropriate permissions

### Local Development

1. **Clone the repository**

   ```bash
   git clone <repository-url>
   cd BibleVerse
   ```
2. **Configure AWS credentials**

   ```bash
   aws configure
   ```
3. **Install local tools**

   ```bash
   mise install
   ```
4. **Build the application**

   ```bash
   mvn clean package
   ```
5. **Synthesize CDK template**

   ```bash
   cd .infrastructure
   pnpm install
   pnpm cdk synth
   ```

### Deployment

1. **Deploy to AWS**

   ```bash
   cd .infrastructure
   pnpm cdk deploy
   ```
2. **Verify deployment**

   ```bash
   curl https://bibleverse.thonbecker.com/about
   ```

### Infrastructure Components

The CDK stack creates:
- **Lambda Functions**: Two functions for different endpoints
- **API Gateway v2**: HTTP API with custom domain
- **S3 Bucket**: Data storage for Bible text files
- **Route 53**: DNS configuration
- **ACM Certificate**: SSL certificate for HTTPS

## Development

### Code Quality

The project uses several tools to maintain code quality:
- **Spotless**: Code formatting with Palantir Java style
- **SortPOM**: Maven POM file organization
- **Maven Enforcer**: Blocks AWS SDK for Java v1 service artifacts
- **Lombok**: Reduces boilerplate code

### CI/CD

GitHub Actions deploys on pushes to `main`. AWS authentication uses GitHub OIDC and the `AWS_ROLE_TO_ASSUME` repository secret, which points to the deploy role ARN.

### Project Structure

```
BibleVerse/
├── src/main/java/com/thonbecker/bibleverse/
│   ├── functions/           # Spring Cloud Functions
│   │   ├── AboutHandler.java
│   │   └── RandomBibleVerseHandler.java
│   ├── model/              # Data models
│   │   ├── AboutResponse.java
│   │   └── RandomBibleVerseResponse.java
│   └── service/            # Business logic
│       └── FileService.java
├── .infrastructure/        # AWS CDK code
│   └── lib/
│       └── bibleverse-stack.ts
└── target/                # Build artifacts
```

### Testing

Run the test suite:

```bash
mvn test
```

### Code Formatting

Format code using Spotless:

```bash
mvn spotless:apply
```

## Recent Improvements

### API Gateway Content Type Fix

**Issue**: API Gateway was not returning `Content-Type: application/json` headers.

**Solution**: Added response parameters to HTTP Lambda integrations in the CDK stack:

```typescript
new HttpLambdaIntegration('Integration', function, {
  responseParameters: {
    '200': {
      'Content-Type': "'application/json'"
    },
    '500': {
      'Content-Type': "'application/json'"
    }
  }
})
```

### Handler Improvements

- **AboutHandler**: Now returns `AboutResponse` object instead of manually serialized string
- **Consistent JSON handling**: Both handlers now use Spring's automatic JSON serialization
- **Better error handling**: Added proper error response configurations

## Monitoring and Logs

- **CloudWatch Logs**: Lambda function logs with 3-day retention
- **API Gateway Access Logs**: HTTP request/response logging
- **Health Monitoring**: `/about` endpoint for service health checks

## Security

- **HTTPS Only**: All endpoints use SSL/TLS encryption
- **CORS Configuration**: Properly configured for web applications
- **S3 Security**: Private bucket with IAM-based access control
- **Lambda Security**: Minimal IAM permissions following least privilege principle

## Bible Data Sources

Bible text files are stored as XML in `.infrastructure/lib/data/files/` and deployed to S3.

|          Translation           |       File        |                                                     Source                                                      |
|--------------------------------|-------------------|-----------------------------------------------------------------------------------------------------------------|
| CSB (Christian Standard Bible) | `csb/bible.xml`   | [Beblia/Holy-Bible-XML-Format](https://github.com/Beblia/Holy-Bible-XML-Format/blob/master/EnglishCSBBible.xml) |
| Greek (Neophytos Vamvas 1770)  | `greek/bible.xml` | [Beblia/Holy-Bible-XML-Format](https://github.com/Beblia/Holy-Bible-XML-Format/blob/master/GreekBible.xml)      |

## License

This project is licensed under the MIT License.

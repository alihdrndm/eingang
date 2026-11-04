# Eingang: XRechnung and ZUGFeRD e-invoice validation and an inbox for supplier invoices

[![CI](https://github.com/alihdrndm/eingang/actions/workflows/ci.yml/badge.svg)](https://github.com/alihdrndm/eingang/actions/workflows/ci.yml)

**Eingang** is an open-source inbox for supplier invoices (_Eingangsrechnungen_) built for small German businesses. It checks whether each invoice is a valid **E-Rechnung**, validating **XRechnung** and **ZUGFeRD / Factur-X** files against the official **KoSIT** rules for **EN 16931**. It reads the data out of plain PDFs with an AI model and shows where every value came from. It also catches duplicate invoices and changed bank details, routes each invoice through four-eyes approval, and exports it for the tax advisor.

**Live demo: [eingang.vercel.app](https://eingang.vercel.app)**

## Features

- **E-Rechnung validation** – XRechnung and ZUGFeRD / Factur-X files are validated against the official KoSIT rules for EN 16931.
- **AI-powered PDF extraction** – Plain PDF invoices are read with an AI model; every extracted value is annotated with its source location.
- **Duplicate detection** – Catches duplicate invoices before they are processed.
- **Bank detail change alerts** – Flags invoices whose bank details differ from previously seen ones.
- **Four-eyes approval** – Each invoice must be approved by a second person before it is finalized.
- **Tax advisor export** – Export processed invoices in a format suitable for your tax advisor.

## Getting started

### Prerequisites

- Node.js 20+
- Docker (optional, for local development)

### Installation

```bash
git clone https://github.com/alihdrndm/eingang.git
cd eingang
npm install
```

### Configuration

Copy the example environment file and adjust the values:

```bash
cp .env.example .env
```

Key variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `AI_API_KEY` | API key for the AI model used for PDF extraction |
| `AI_MODEL` | Name of the AI model to use |
| `JWT_SECRET` | Secret for signing JWT tokens |
| `PORT` | Port the server listens on (default: 3000) |

### Running

```bash
# Development
npm run dev

# Production build
npm run build
npm start
```

### Database migrations

```bash
npm run migrate
```

## Contributing

Contributions are welcome! Please read the following before submitting a pull

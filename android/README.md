# Switchboard Android Client

This directory contains the native Android application for Switchboard.

## Source of Truth

The **[root README.md](../README.md)** is the canonical source of truth for the overall Switchboard solution, development environment, and deployment instructions. Please refer to it for:

- **Overall Architecture**: How the gateway and Android client interact.
- **Environment Setup**: Python server configuration and Firebase requirements.
- **Troubleshooting**: Manual Wifi pairing instructions and ADB service management.

## Android Subproject Context

While the root documentation covers the high-level flow, this directory contains the Gradle build logic and source code for the mobile app.

### Key Resources
- **[MainActivity.kt](app/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt)**: Core UI and navigation.
- **[MainViewModel.kt](app/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt)**: Firebase synchronization logic.
- **[install-client.ps1](../scripts/install-client.ps1)**: The recommended script for building and deploying the app from the command line.

### Manual Wifi Pairing
If you need to manually pair your device over Wifi, refer to the **Troubleshooting** section in the [main README.md](../README.md#troubleshooting--manual-wi-fi-pairing).

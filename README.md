# AutoTitrator

A comprehensive automated titration system built for Raspberry Pi, featuring a modern web interface, support for Atlas Scientific sensors, and precision pump control.

## 🚀 Features

*   **Automated Titration**: Support for both Volumetric (fixed volume) and Endpoint (target pH/EC) variations.
*   **Modern Web Dashboard**: Real-time monitoring of pH, Conductivity, and Pump status.
*   **Hardware Integration**: Native support for:
    *   Atlas Scientific EZO-pH Circuit
    *   Atlas Scientific EZO-EC Circuit
    *   GPIO-controlled Peristaltic Pumps (Active LOW Relays)
*   **Calibration Wizards**: Guided interfaces for calibrating PH and Conductivity probes.
*   **Pump Calibration**: Tools to measure and calibrate flow rate.
*   **Data Logging**: Automatic recording of titration experiments with CSV export and visualization.
*   **Project Management**: Organize titrations into projects.
*   **Smart Fallback**: Automatically switches to "Mock Mode" when running on non-Raspberry Pi hardware for easy development.

## 🛠 Hardware Requirements

*   **Raspberry Pi** (Any model with 40-pin GPIO and I2C headers)
*   **Atlas Scientific EZO-pH Circuit** (Default I2C Address: 0x63)
*   **Atlas Scientific EZO-EC Circuit** (Default I2C Address: 0x64)
*   **Peristaltic Pump** (Controlled via Relay on GPIO 23, Active LOW logic)
*   **12V Power Supply** (for Pump)
*   **Relay Module** (To interface the 12V pump with 3.3V Pi GPIO)

## 📦 Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/yourusername/autotitrator.git
    cd autotitrator
    ```

2.  **Install Dependencies**
    ```bash
    pip3 install -r requirements.txt
    ```

3.  **Enable I2C (Raspberry Pi Only)**
    *   Run `sudo raspi-config`
    *   Navigate to **Interface Options** -> **I2C** -> **Enable**
    *   Reboot the Pi.

## 🖥 Usage

### Running the Application
Execute the runner script from the root directory:

```bash
python3 run.py
```

*   The application will start a web server on port `5000`.
*   Access the dashboard by navigating to `http://<pi-ip-address>:5000` in your browser.
*   If running locally on a computer (Development Mode), visit `http://127.0.0.1:5000`.

### Development Mode (Mock Hardware)
The system detects if `RPi.GPIO` or `AtlasI2C` drivers are missing and automatically enters **DEV Mode**.
*   **Mock Pump**: Simulates pump activation logs without toggling GPIO.
*   **Mock Probes**: Generates random fluctuation data for pH and EC to simulate readings.

## 📂 Project Structure

```
auto_titrator/
├── run.py                 # Application Entry Point
├── config.json            # Persisted Settings (Flow rate, etc.)
├── requirements.txt       # Python Dependencies
├── src/
│   ├── app.py             # Flask Web Server & API Routes
│   ├── utils.py           # Configuration & Helper Utilities
│   ├── hardware/          # Hardware Abstraction Layer
│   │   ├── manager.py     # Hardware Singleton & Mode Selection
│   │   ├── pump.py        # Pump Drivers (Mock & Real on GPIO 23)
│   │   ├── probes.py      # Probe Drivers (Mock & Real)
│   │   └── drivers/       # Low-level I2C Drivers
│   ├── titration/         # Titration Logic Engine
│   └── static/            # Frontend Assets (CSS, JS, Titration Data)
│       └── titrations/    # CSV Data Logs
└── templates/             # HTML Templates
```

## 🔧 Configuration

Settings such as **Default Flow Rate**, **Wait Times**, and **Safety Limits** can be adjusted directly from the **Settings** page in the web interface. These are saved to `config.json`.

## 🤝 Contributing

1.  Fork the repository
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

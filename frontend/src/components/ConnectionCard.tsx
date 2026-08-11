import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  closeSerial,
  humanizeApiError,
  listSerialPorts,
  openSerial,
  SerialPortInfo,
  SerialStatus,
} from "../api/rest";
import { formatBytes } from "../format";
import { Card } from "./shell/Card";

/**
 * Port + baud picker and connect/disconnect controls.
 *
 * Ported from the connection half of DUT_browser's `SettingsSection` /
 * `.conn-*` markup. Cut: replay mode (M1 reads a real port only) and the
 * terminal-mode toggle (M2).
 */

// SPEC §2: 115200 default, must sustain up to 921600.
const BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];

type Props = {
  serial: SerialStatus | null;
  onChanged: () => void;
};

export default function ConnectionCard({ serial, onChanged }: Props) {
  const [ports, setPorts] = useState<SerialPortInfo[]>([]);
  const [selectedPort, setSelectedPort] = useState("");
  const [baudrate, setBaudrate] = useState(115200);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connected = serial?.connected ?? false;

  // The open port is not always in the enumerated list (a PTY, or a device that
  // disappeared from the scan). Fold it in so the picker shows what is actually
  // open instead of silently falling back to the first option.
  const options = useMemo(() => {
    if (!selectedPort || ports.some((port) => port.device === selectedPort)) {
      return ports;
    }
    return [{ device: selectedPort, description: "", hwid: "" }, ...ports];
  }, [ports, selectedPort]);

  const refreshPorts = useCallback(async () => {
    try {
      const found = await listSerialPorts();
      setPorts(found);
      setSelectedPort((prev) => {
        if (prev && found.some((port) => port.device === prev)) {
          return prev;
        }
        return found[0]?.device ?? prev;
      });
    } catch (cause) {
      setError(humanizeApiError(cause));
    }
  }, []);

  useEffect(() => {
    void refreshPorts();
  }, [refreshPorts]);

  // Adopt whatever the backend already has open (e.g. after a page reload).
  useEffect(() => {
    if (serial?.connected && serial.port) {
      setSelectedPort(serial.port);
      setBaudrate(serial.baudrate);
    }
  }, [serial?.connected, serial?.port, serial?.baudrate]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (connected) {
        await closeSerial();
      } else {
        await openSerial(selectedPort, baudrate);
      }
    } catch (cause) {
      setError(humanizeApiError(cause));
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  return (
    <Card
      title="Serial port"
      subtitle="Raw logging starts the moment the port opens and never stops while it is open."
      actions={
        <button type="button" className="btn" onClick={() => void refreshPorts()} disabled={busy}>
          Re-scan
        </button>
      }
    >
      <form className="conn-form" onSubmit={handleSubmit}>
        <div className="conn-row">
          <label className="conn-label" htmlFor="port-select">
            Port
          </label>
          {options.length > 0 ? (
            <select
              id="port-select"
              className="conn-port"
              value={selectedPort}
              onChange={(event) => setSelectedPort(event.target.value)}
              disabled={connected || busy}
            >
              {options.map((port) => (
                <option key={port.device} value={port.device}>
                  {port.device}
                  {port.description ? ` — ${port.description}` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              id="port-select"
              className="conn-port"
              value={selectedPort}
              onChange={(event) => setSelectedPort(event.target.value)}
              placeholder="/dev/ttyUSB0"
              disabled={connected || busy}
            />
          )}

          <label className="conn-label" htmlFor="baud-select">
            Baud
          </label>
          <select
            id="baud-select"
            className="conn-input conn-baud"
            value={baudrate}
            onChange={(event) => setBaudrate(Number(event.target.value))}
            disabled={connected || busy}
          >
            {BAUD_RATES.map((rate) => (
              <option key={rate} value={rate}>
                {rate}
              </option>
            ))}
          </select>

          <button
            type="submit"
            className={`btn ${connected ? "danger" : "primary"}`}
            disabled={busy || (!connected && !selectedPort)}
          >
            {connected ? "Disconnect" : "Connect"}
          </button>
        </div>

        {error ? <div className="conn-error">{error}</div> : null}

        <div className="conn-meta">
          <span>{connected ? `open ${serial?.port} @ ${serial?.baudrate}` : "port closed"}</span>
          {serial?.log_name ? <span>log {serial.log_name}</span> : null}
          {connected ? <span>{formatBytes(serial?.bytes_written ?? 0)} captured</span> : null}
        </div>

        {serial?.last_error ? <div className="conn-error">{serial.last_error}</div> : null}
      </form>
    </Card>
  );
}

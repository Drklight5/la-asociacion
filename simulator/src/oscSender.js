import { Client } from "node-osc";

// Direcciones OSC que Pure Data debe escuchar (netreceive -u -b <port> -> oscparse -> route).
export const OSC_ADDRESSES = {
  delta: "/eeg/wave/delta",
  theta: "/eeg/wave/theta",
  beta: "/eeg/wave/beta",
  alfa: "/eeg/wave/alfa",
  gamma: "/eeg/wave/gamma",
  bpm: "/eeg/bpm",
  movement: "/eeg/movement",
  moment: "/eeg/moment",
  // Extension: ejes crudos de giro (grados/s) y acelerometro (g), para viz/ y
  // para que el equipo de Pd los mapee a la musica. Aditivas -- Pd las rutea o
  // no, no rompe nada. Ver README.md del proyecto.
  gyroX: "/eeg/gyro/x",
  gyroY: "/eeg/gyro/y",
  gyroZ: "/eeg/gyro/z",
  accelX: "/eeg/accel/x",
  accelY: "/eeg/accel/y",
  accelZ: "/eeg/accel/z",
};

export class OscFrameSender {
  constructor(host, port) {
    this.client = new Client(host, port);
    this.host = host;
    this.port = port;
  }

  send(frame) {
    const { waves, bpm, movement, moment, gyro, accel } = frame;
    this.client.send(OSC_ADDRESSES.delta, waves.delta);
    this.client.send(OSC_ADDRESSES.theta, waves.theta);
    this.client.send(OSC_ADDRESSES.beta, waves.beta);
    this.client.send(OSC_ADDRESSES.alfa, waves.alfa);
    this.client.send(OSC_ADDRESSES.gamma, waves.gamma);
    this.client.send(OSC_ADDRESSES.bpm, bpm);
    this.client.send(OSC_ADDRESSES.movement, movement);
    this.client.send(OSC_ADDRESSES.moment, moment);
    if (gyro) {
      this.client.send(OSC_ADDRESSES.gyroX, gyro.x);
      this.client.send(OSC_ADDRESSES.gyroY, gyro.y);
      this.client.send(OSC_ADDRESSES.gyroZ, gyro.z);
    }
    if (accel) {
      this.client.send(OSC_ADDRESSES.accelX, accel.x);
      this.client.send(OSC_ADDRESSES.accelY, accel.y);
      this.client.send(OSC_ADDRESSES.accelZ, accel.z);
    }
  }

  close() {
    this.client.close();
  }
}

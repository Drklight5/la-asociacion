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
};

export class OscFrameSender {
  constructor(host, port) {
    this.client = new Client(host, port);
    this.host = host;
    this.port = port;
  }

  send(frame) {
    const { waves, bpm, movement, moment } = frame;
    this.client.send(OSC_ADDRESSES.delta, waves.delta);
    this.client.send(OSC_ADDRESSES.theta, waves.theta);
    this.client.send(OSC_ADDRESSES.beta, waves.beta);
    this.client.send(OSC_ADDRESSES.alfa, waves.alfa);
    this.client.send(OSC_ADDRESSES.gamma, waves.gamma);
    this.client.send(OSC_ADDRESSES.bpm, bpm);
    this.client.send(OSC_ADDRESSES.movement, movement);
    this.client.send(OSC_ADDRESSES.moment, moment);
  }

  close() {
    this.client.close();
  }
}

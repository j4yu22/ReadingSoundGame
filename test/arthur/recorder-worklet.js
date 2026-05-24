class ArthurRecorderProcessor extends AudioWorkletProcessor {
  process(inputs, outputs) {
    const input = inputs[0];
    const output = outputs[0];

    if (input && input[0]) {
      this.port.postMessage(input[0].slice(0));
      if (output && output[0]) {
        output[0].set(input[0]);
      }
    }

    return true;
  }
}

registerProcessor("arthur-recorder", ArthurRecorderProcessor);

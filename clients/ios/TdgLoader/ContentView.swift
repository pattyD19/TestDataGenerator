import SwiftUI

struct ContentView: View {
    @StateObject private var model = LoaderModel()
    @State private var confirmWipe = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Control plane") {
                    TextField("http://192.168.1.10:8722", text: $model.host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("Pairing code", text: $model.code)
                        .keyboardType(.numberPad)
                    Button("Find pack") { model.pair() }
                        .disabled(model.busy)
                }

                if !model.status.isEmpty {
                    Section("Pack") {
                        Text(model.status).font(.headline)
                        if !model.detail.isEmpty {
                            Text(model.detail)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        if model.busy && model.progress > 0 {
                            ProgressView(value: model.progress)
                        }
                    }
                }

                Section {
                    Button("Fill the library") { model.load() }
                        .disabled(!model.canLoad || model.busy)
                    if model.busy {
                        Button("Stop", role: .cancel) { model.stop() }
                    }
                }

                Section("Loaded on this device") {
                    if model.loadedJobs.isEmpty {
                        Text("Nothing yet.").foregroundStyle(.secondary)
                    } else {
                        ForEach(model.loadedJobs, id: \.job) { entry in
                            HStack {
                                Text(entry.job).font(.body.monospaced())
                                Spacer()
                                Text("\(entry.count) assets")
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Button("Remove everything this app added", role: .destructive) {
                            confirmWipe = true
                        }
                        .disabled(model.busy)
                    }
                }
            }
            .navigationTitle("TDG Loader")
            .confirmationDialog(
                "Remove \(model.loadedJobs.reduce(0) { $0 + $1.count }) assets?",
                isPresented: $confirmWipe, titleVisibility: .visible
            ) {
                Button("Remove", role: .destructive) { model.wipe() }
                Button("Cancel", role: .cancel) { }
            } message: {
                Text("Deletes only what this app added. Photos will ask you to "
                   + "confirm as well.")
            }
        }
    }
}

/**
 * zimfast - Fast ZIM file reader for Python
 *
 * A thin pybind11 wrapper around libzim's C++ API, exposing
 * cluster-order iteration which is not available in python-libzim.
 *
 * This enables parallel reading by allowing multiple threads to
 * access different index ranges concurrently.
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <zim/archive.h>
#include <zim/entry.h>
#include <zim/item.h>
#include <string>
#include <optional>
#include <iostream>
#include <cstdlib>
#include <csignal>
#include <atomic>

namespace py = pybind11;

// Global debug flag - set ZIMFAST_DEBUG=1 to enable
static bool g_debug = false;

// Track last processed index for crash debugging
static std::atomic<size_t> g_last_idx{0};
static std::atomic<int> g_last_step{0};  // 0=start, 1=getEntry, 2=isRedirect, 3=getItem, 4=getData, 5=makeTuple

// Step names for debugging
static const char* step_names[] = {
    "start",
    "getEntryByClusterOrder",
    "isRedirect",
    "getMetadata",
    "getItem",
    "getData",
    "copyData",
    "makeTuple",
    "done"
};

// Signal handler for segfaults
static void segfault_handler(int sig) {
    std::cerr << "\n*** SEGFAULT in zimfast ***" << std::endl;
    std::cerr << "Last entry index: " << g_last_idx.load() << std::endl;
    std::cerr << "Last step: " << g_last_step.load()
              << " (" << step_names[g_last_step.load()] << ")" << std::endl;
    std::cerr << "Signal: " << sig << std::endl;

    // Re-raise to get core dump
    signal(sig, SIG_DFL);
    raise(sig);
}

static void init_debug() {
    static bool initialized = false;
    if (!initialized) {
        const char* debug_env = std::getenv("ZIMFAST_DEBUG");
        g_debug = (debug_env != nullptr && std::string(debug_env) == "1");

        // Install signal handler
        signal(SIGSEGV, segfault_handler);
        signal(SIGABRT, segfault_handler);

        if (g_debug) {
            std::cerr << "[zimfast] Debug mode enabled" << std::endl;
        }
        initialized = true;
    }
}

#define DEBUG_LOG(msg) if (g_debug) { std::cerr << "[zimfast] " << msg << std::endl; std::cerr.flush(); }

class ZimReader {
private:
    zim::Archive archive;
    std::string archive_path;

public:
    explicit ZimReader(const std::string& path) : archive(path), archive_path(path) {
        init_debug();
        DEBUG_LOG("Opened archive: " << path);
    }

    /**
     * Get the number of user entries in the archive.
     * This excludes internal entries like metadata.
     */
    size_t entry_count() const {
        return archive.getEntryCount();
    }

    /**
     * Get the total number of all entries including internal ones.
     */
    size_t all_entry_count() const {
        return archive.getAllEntryCount();
    }

    /**
     * Get last processed index (for debugging crashes)
     */
    static size_t get_last_idx() {
        return g_last_idx.load();
    }

    /**
     * Get last step (for debugging crashes)
     */
    static int get_last_step() {
        return g_last_step.load();
    }

    /**
     * Get step name
     */
    static std::string get_step_name(int step) {
        if (step >= 0 && step <= 8) {
            return step_names[step];
        }
        return "unknown";
    }

    /**
     * Get an entry by its cluster order index.
     *
     * Cluster order is the physical storage order in the ZIM file,
     * which is optimal for sequential disk access.
     *
     * Returns a tuple of (path, title, content_bytes, mimetype) or None if redirect.
     */
    py::object get_entry(size_t idx) {
        g_last_idx.store(idx);
        g_last_step.store(0);

        try {
            // Step 1: Get entry by cluster order
            g_last_step.store(1);
            DEBUG_LOG("get_entry(" << idx << "): getEntryByClusterOrder");
            auto entry = archive.getEntryByClusterOrder(idx);

            // Step 2: Check if redirect
            g_last_step.store(2);
            DEBUG_LOG("get_entry(" << idx << "): isRedirect");
            if (entry.isRedirect()) {
                DEBUG_LOG("get_entry(" << idx << "): is redirect, returning None");
                return py::none();
            }

            // Step 3: Get metadata FIRST (before getData which might affect buffers)
            g_last_step.store(3);
            DEBUG_LOG("get_entry(" << idx << "): getMetadata");
            std::string path = entry.getPath();
            std::string title = entry.getTitle();

            // Step 4: Get item
            g_last_step.store(4);
            DEBUG_LOG("get_entry(" << idx << "): getItem");
            auto item = entry.getItem();
            std::string mimetype = item.getMimetype();

            // Step 5: Get data and IMMEDIATELY copy to std::string
            // This ensures we own the data before any other libzim calls
            g_last_step.store(5);
            DEBUG_LOG("get_entry(" << idx << "): getData");
            auto blob = item.getData();

            // Make an explicit copy of the data to avoid use-after-free
            g_last_step.store(6);
            std::string content_copy(blob.data(), blob.size());
            DEBUG_LOG("get_entry(" << idx << "): copied data (" << content_copy.size() << " bytes)");

            // Step 7: Create Python tuple with our owned copies
            g_last_step.store(7);
            DEBUG_LOG("get_entry(" << idx << "): makeTuple");

            auto result = py::make_tuple(
                path,
                title,
                py::bytes(content_copy.data(), content_copy.size()),
                mimetype
            );

            g_last_step.store(8);
            DEBUG_LOG("get_entry(" << idx << "): done");
            return result;

        } catch (const std::exception& e) {
            DEBUG_LOG("get_entry(" << idx << "): exception at step "
                      << g_last_step.load() << ": " << e.what());
            return py::none();
        } catch (...) {
            DEBUG_LOG("get_entry(" << idx << "): unknown exception at step "
                      << g_last_step.load());
            return py::none();
        }
    }

    /**
     * Get entry metadata only (no content) - faster for scanning.
     * Returns (path, title, mimetype, is_redirect) or None on error.
     */
    py::object get_entry_metadata(size_t idx) {
        g_last_idx.store(idx);

        try {
            auto entry = archive.getEntryByClusterOrder(idx);
            bool is_redirect = entry.isRedirect();

            std::string mimetype;
            if (!is_redirect) {
                mimetype = entry.getItem().getMimetype();
            }

            return py::make_tuple(
                entry.getPath(),
                entry.getTitle(),
                mimetype,
                is_redirect
            );
        } catch (const std::exception& e) {
            DEBUG_LOG("get_entry_metadata(" << idx << "): exception: " << e.what());
            return py::none();
        } catch (...) {
            return py::none();
        }
    }

    /**
     * Check if an entry at the given index is a redirect.
     */
    bool is_redirect(size_t idx) {
        try {
            return archive.getEntryByClusterOrder(idx).isRedirect();
        } catch (...) {
            return true;  // Treat errors as "skip this entry"
        }
    }
};


PYBIND11_MODULE(zimfast, m) {
    m.doc() = "Fast ZIM file reader with cluster-order iteration support";

    py::class_<ZimReader>(m, "ZimReader")
        .def(py::init<const std::string&>(), py::arg("path"),
             "Open a ZIM file for reading")
        .def("entry_count", &ZimReader::entry_count,
             "Get the number of user entries")
        .def("all_entry_count", &ZimReader::all_entry_count,
             "Get the total number of all entries including internal ones")
        .def("get_entry", &ZimReader::get_entry, py::arg("idx"),
             "Get entry by cluster order index. Returns (path, title, content, mimetype) or None")
        .def("get_entry_metadata", &ZimReader::get_entry_metadata, py::arg("idx"),
             "Get entry metadata only (path, title, mimetype, is_redirect) - no content")
        .def("is_redirect", &ZimReader::is_redirect, py::arg("idx"),
             "Check if entry at index is a redirect")
        .def_static("get_last_idx", &ZimReader::get_last_idx,
             "Get last processed entry index (for crash debugging)")
        .def_static("get_last_step", &ZimReader::get_last_step,
             "Get last processing step (for crash debugging)")
        .def_static("get_step_name", &ZimReader::get_step_name, py::arg("step"),
             "Get name of processing step");

    m.attr("STEP_START") = 0;
    m.attr("STEP_GET_ENTRY") = 1;
    m.attr("STEP_IS_REDIRECT") = 2;
    m.attr("STEP_GET_METADATA") = 3;
    m.attr("STEP_GET_ITEM") = 4;
    m.attr("STEP_GET_DATA") = 5;
    m.attr("STEP_COPY_DATA") = 6;
    m.attr("STEP_MAKE_TUPLE") = 7;
    m.attr("STEP_DONE") = 8;
}

#pragma once

#include <vector>
#include <queue>
#include <cstdint>

namespace microstructure {

enum class EventType {
    MARKET_DATA,
    ORDER,
    FILL
};

struct Event {
    int64_t timestamp_mu;
    EventType type;

    // MARKET_DATA fields
    double bid_price;
    double bid_volume;
    double ask_price;
    double ask_volume;

    // ORDER & FILL fields
    int order_id;
    int side; // 1 for buy, -1 for sell
    double quantity;
    double price;

    bool operator<(const Event& other) const {
        // priority_queue in C++ is a max-heap.
        // To process lowest timestamp first (min-heap behavior), we return true when this > other.
        return timestamp_mu > other.timestamp_mu;
    }
};

struct Trade {
    int64_t timestamp_mu;
    int order_id;
    int side;
    double quantity;
    double price;
};

class Backtester {
public:
    Backtester();

    void add_market_data(int64_t timestamp_mu, double bid_price, double bid_volume, double ask_price, double ask_volume);
    void place_order(int64_t timestamp_mu, int order_id, int side, double quantity);
    void run();

    std::vector<Trade> get_trades() const;
    double get_pnl() const;

private:
    std::priority_queue<Event> events_;
    int64_t current_time_;
    double pnl_;
    double position_;

    double best_bid_;
    double best_ask_;
    double bid_vol_;
    double ask_vol_;

    std::vector<Trade> trades_;

    void process_market_data(const Event& e);
    void process_order(const Event& e);
    void process_fill(const Event& e);
};

} // namespace microstructure
